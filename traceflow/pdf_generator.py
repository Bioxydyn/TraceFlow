import os
import subprocess  # noqa S404
import tempfile
from contextlib import contextmanager
import shutil
from pkgutil import get_data
import random
import re
import string
from typing import Generator, Optional

import latex.jinja2
from PIL import Image

try:
    import cairosvg
except OSError as exc:
    cairosvg = None  # type: ignore[assignment]
    _CAIROSVG_IMPORT_ERROR = exc
else:
    _CAIROSVG_IMPORT_ERROR = None

from traceflow.parser import Document, RequirementDocument, parse_markdown
from traceflow.version import __version__

_latex_jinja2_env = latex.jinja2.make_env()

PLAYWRIGHT_MAX_FRAMES = 9


def load_resource(package: str, filename: str) -> bytes:
    data = get_data(package, filename)
    assert data is not None
    return data


@contextmanager
def isolated_filesystem(temp_path: Optional[str] = None) -> Generator:
    current_directory = os.getcwd()

    user_specified_path = temp_path is not None

    if not user_specified_path:
        temp_path = tempfile.mkdtemp()
    assert temp_path is not None

    try:
        os.chdir(temp_path)
        yield

    finally:
        os.chdir(current_directory)

        if not user_specified_path:
            shutil.rmtree(temp_path)


class PdfReport():
    @staticmethod
    def process_text_impl(text: str, unique_ids: set[str]) -> str:

        # Replace any instances of a unique ID within the text to a link to the ID.
        # 1. Explode text into words
        words = text.split()

        # 2. For each word, check if it is a unique ID. unique_ids is a set, so this is O(1)
        for index, word in enumerate(words):
            altered_word = word.replace(":", "")
            if altered_word in unique_ids:
                words[index] = f"\\hyperref[{altered_word}]{{{word}}}"

        # 3. Rebuild the text
        new_text = " ".join(words)

        # 4. Did we originally have whitespace at the start or end - if so, add it back
        if text.startswith(" "):
            new_text = " " + new_text
        if text.endswith(" "):
            new_text += " "
        if text.startswith("\n"):
            new_text = "\n" + new_text
        if text.endswith("\n"):
            new_text += "\n"

        # Regular expression to find text wrapped in backticks
        inline_code_pattern = r'`([^`]*)`'

        # Replace the text wrapped in backticks with LaTeX inline code
        new_text = re.sub(inline_code_pattern, r'\\texttt{\1}', new_text)

        return new_text.replace(r"&", r"\&").replace(r"_", r"\_")

    def process_text(self, text: str) -> str:
        return self.process_text_impl(text, self.unique_ids)

    def build_traceability_matrix(self, req_page: RequirementDocument) -> str:
        # Display the traceability matrix
        table = "\\subsection{Traceability Matrix}\n\n"

        # Create the table data
        columns: list[str] = []

        for requirement in req_page.items:
            if requirement.test_ids is not None:
                columns += requirement.test_ids
        columns = list(set(columns))
        columns.sort()
        large_matrix = len(columns) > 15

        # Split columns into chunks so that we don't exceed the maximum number of columns
        chunked_columns = []
        max_columns = 17
        while len(columns) > 0:
            chunked_columns.append(columns[:max_columns])
            columns = columns[max_columns:]

        for chunk in chunked_columns:
            if large_matrix:
                # This is a large matrix. We put the table on its own page, and that page in landscape mode
                table += "\\newpage\n"
                table += "\\begin{landscape}\n"

            table += "\\setlength\\tabcolsep{0pt}\n"
            # Build the table. There should be a tick in the cell if the test is linked to the requirement,
            # otherwise it should be empty
            table += "\\rowcolors{2}{gray!25}{white}\n"
            table += "\\begin{table}[h]\n"
            table += "\\centering\n"
            table += "\\begin{tabular}{@{}c@{}" + "@{}>{\\centering\\arraybackslash}m{1cm}@{}" * len(chunk) + "}\n"
            table += "\\hline\n"

            # Header row with test ids
            header = "\\diagbox{\\textbf{\\textit{Req ID}}}{\\textbf{\\textit{Test ID}}}"
            for test_id in chunk:
                header += " & \\rot{\\hyperref[" + test_id + "]{" + test_id + "}}"
            header += " \\\\\n\\hline\n"
            table += header

            # For each requirement, generate a row
            for r in req_page.items:
                row = ""
                if not r.test_ids:
                    row += "\\rowcolor{red}"
                row += "\\hyperref[" + r.req_id + "]{" + r.req_id + "}"
                for test_id in chunk:
                    if test_id in r.test_ids:
                        row += " & \\hyperref[" + test_id + "]{" + "$\\checkmark$}"
                    else:
                        row += " & "
                row += " \\\\\n\\hline\n"
                table += row

            table += "\\end{tabular}\n"
            table += "\\end{table}\n\n"

            if large_matrix:
                table += "\\end{landscape}\n"
                table += "\\newpage\n"

        return table

    def md_to_latex(self, items: list[dict]) -> str:

        def handle_paragraph(item: dict) -> str:
            latex = ["\n"]
            for child in item["children"]:
                if child["type"] == "text":
                    latex.append(self.process_text(child["text"]))
                else:
                    latex.append(handle_item(child))
            return "".join(latex)

        def handle_link(item: dict) -> str:
            link_url = item["link"]
            link_text = self.md_to_latex(item["children"])
            return f"\\href{{{link_url}}}{{{link_text}}}"

        def handle_heading(item: dict) -> str:
            level = item["level"]
            return "\\" + "sub" * (level - 1) + "section{" + self.process_text(item["children"][0]["text"]) + "}"

        def handle_list(item: dict) -> str:
            latex = ["\\begin{itemize}"]
            for list_item in item["children"]:
                latex.append("\n\\item ")
                for child in list_item["children"]:
                    if child["type"] == "text":
                        latex.append(self.process_text(child["text"]))
                    if child["type"] == "block_text":
                        try:
                            latex.append(self.md_to_latex(child["children"]))
                        except IndexError:
                            print("Warning, empty block text:", child)

            latex.append("\n\\end{itemize}")
            return "".join(latex)

        def handle_image(item: dict) -> str:
            url = item["src"]
            latex = [
                '\n\\begin{figure}[h]',
                '\n\\centering',
                f'\n\\includegraphics[width=0.5\\textwidth]{{{url}}}',
                f'\n\\caption{{{self.process_text(item["alt"])}}}',
                '\n\\end{figure}',
            ]
            return ''.join(latex)

        def handle_block_code(item: dict) -> str:
            code_type = None
            if "info" in item:
                code_type = item["info"]

            if code_type == "mermaid":
                return handle_mermaid(item)
            if code_type == "raw":
                return item["text"]
            if code_type == "manualtest":
                return handle_manual_test(item)
            if code_type == "testcoverpage":
                return handle_test_cover_page(item)
            if code_type == "autotest":
                return handle_auto_test(item)
            if code_type == "autoplaywright":
                return handle_playwright_test(item)
            return handle_code(item)

        def handle_test_cover_page(_: dict) -> str:
            return r"""
\begin{table}[h]
\renewcommand{\arraystretch}{2} % Increases the height of each row
\arrayrulecolor{gray} % Set the color of the horizontal and vertical lines to gray
\begin{tabular}{|>{\columncolor{gray!30}}m{0.45\linewidth}|m{0.45\linewidth}|}
\hline
\textbf{Tester} & \\
\hline
\textbf{Test Date} & \\
\hline
\textbf{Result} & \\
\hline
\textbf{Observations} \vspace*{3\baselineskip} & \\ % Add vertical space within this cell
\hline
\end{tabular}
\end{table}

\vspace{1cm}
\newpage

"""

        def handle_auto_test(item: dict) -> str:
            content: str = item["text"]
            assert content is not None
            assert isinstance(content, str)

            # Content is a script, to be executed. We need to capture the exit code and the stdout & stderr. We classify
            # the test as a pass if the exit code is 0, and a fail otherwise. We also capture the stdout and stderr and
            # include them in the report, in full colour using ANSI escape codes.

            # 1. Write the script to a temporary file
            script_filename = PdfReport.get_temporary_filename(suffix=".sh", force_random=True)
            # Get the full path to the script file
            script_filename = os.path.join(os.getcwd(), script_filename)
            with open(script_filename, "w") as f:
                f.write(content)

            # 2. Execute the script
            content_summary = content.strip().split("\n")[0]
            print(f"Executing test: {content_summary}")
            current_directory = os.getcwd()
            try:
                os.chdir(self.original_working_directory)
                output = subprocess.check_output(["bash", script_filename], stderr=subprocess.STDOUT)  # noqa S603, S607
                exit_code = 0
            except subprocess.CalledProcessError as e:
                output = e.output
                exit_code = e.returncode
            finally:
                os.chdir(current_directory)

            # 3. Convert the output to a string
            output_str: str = output.decode("utf-8")

            if exit_code != 0:
                print(f"Test failed: {content_summary}")
                print(output_str)

            if len(output_str) > 40000:
                output_str = output_str[:40000] + " ... [truncated]"

            def create_latex_markup(is_pass: bool) -> str:
                if is_pass:
                    return r"""\textbf{Pass} \CheckedBox \hspace{2cm} \textbf{Fail} \Square \hspace{2cm} \textbf{Skip} \Square \\
"""  # noqa E501
                return r"""\textbf{Pass} \Square \hspace{2cm} \textbf{Fail} \CheckedBox \hspace{2cm} \textbf{Skip} \Square \\
"""  # noqa E501

            return r"""
\noindent
""" + create_latex_markup(is_pass=exit_code == 0) + "\n" + r"""
\vspace{0.2cm}
\begin{lstlisting}[language=bash, basicstyle=\ttfamily\small, breaklines=true, breakatwhitespace=true, showstringspaces=false, escapeinside={(*}{*)}]
""" + output_str + "\n" + r"""
\end{lstlisting}"""  # noqa E501

        def handle_playwright_test(item: dict) -> str:
            content: str = item.get("text", "")
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                raise ValueError("autoplaywright blocks must include the Playwright test ID")
            test_name = lines[0]
            notes_text = " ".join(lines[1:])
            return self._render_playwright_test_section(test_name, notes_text)

        def handle_manual_test(_: dict) -> str:
            pass_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            fail_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            skip_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            comment_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            return r"""
\noindent
\begin{Form}
\textbf{Pass} \CheckBox[name=""" + pass_id + r"""]{} \hspace{2cm} \textbf{Fail} \CheckBox[name=""" + fail_id + r"""]{} \hspace{2cm} \textbf{Skip} \CheckBox[name=""" + skip_id + r"""]{} \\
\vspace{0.2cm}
\textbf{Comments} \\
\TextField[name=""" + comment_id + r""", multiline=true, width=\linewidth, height=2cm]{}
\end{Form}
        """  # noqa E501

        def handle_code(item: dict) -> str:
            language = None
            if "info" in item:
                language = item["info"]
            code_content = item["text"]

            if language:
                return f"\\begin{{lstlisting}}[language={language}]\n{code_content}\n\\end{{lstlisting}}"
            return f"\\begin{{lstlisting}}\n{code_content}\n\\end{{lstlisting}}"

        def handle_mermaid(item: dict) -> str:

            svg_path = PdfReport.get_temporary_filename(suffix=".svg", force_random=True)
            mmd_path = PdfReport.get_temporary_filename(suffix=".mmd", force_random=True)

            mermaid_code = item["text"]
            with open(mmd_path, "w") as mmd_file:
                mmd_file.write(mermaid_code)

            subprocess.run(["mmdc", "-i", mmd_path, "-o", svg_path], check=True)  # noqa S607, S603

            if cairosvg is None:
                raise RuntimeError(
                    "Rendering mermaid diagrams requires cairosvg, but importing it failed"
                    f" with: {_CAIROSVG_IMPORT_ERROR}"
                )
            # Convert the SVG to PDF
            pdf_path = svg_path.replace(".svg", ".pdf")
            cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
            return handle_image({"src": pdf_path, "alt": "", "title": "", "type": "image"})

        def handle_item(item: dict) -> str:
            handlers = {
                "text": lambda item: self.process_text(item["text"]),
                "paragraph": handle_paragraph,
                "link": handle_link,
                "heading": handle_heading,
                "list": handle_list,
                "image": handle_image,
                "block_code": handle_block_code,
                "blank_line": lambda _: "\n",
                "newline": lambda _: "\n",
                "strong": lambda item: f"\\textbf{{{self.process_text(item['children'][0]['text'])}}}",
                "emphasis": lambda item: f"\\emph{{{self.process_text(item['children'][0]['text'])}}}",
                "softbreak": lambda _: "\n",
                "codespan": lambda item: f"\\texttt{{{self.process_text(item['text'])}}}",
                "linebreak": lambda _: "\n",
            }
            handler = handlers.get(item["type"])
            if handler:
                return handler(item)
            print(f"Unknown item type: {item['type']}")
            print(item)
            return ""

        latex = []
        for item in items:
            latex.append(handle_item(item))
        return "\n".join(latex)

    def _render_playwright_test_section(self, test_name: str, notes_text: str) -> str:
        video_path, log_path, exit_code = self._execute_playwright_test(test_name)
        log_output = self._read_playwright_log(log_path)
        grid_basename = self._build_playwright_grid(video_path)

        def create_latex_markup(is_pass: bool) -> str:
            if is_pass:
                return (
                    r"\textbf{Pass} \CheckedBox \hspace{2cm} \textbf{Fail} \Square "
                    r"\hspace{2cm} \textbf{Skip} \Square \\"
                )
            return (
                r"\textbf{Pass} \Square \hspace{2cm} \textbf{Fail} \CheckedBox "
                r"\hspace{2cm} \textbf{Skip} \Square \\"
            )

        status_block = create_latex_markup(is_pass=exit_code == 0)
        body: list[str] = [r"\noindent", status_block, r"\vspace{0.2cm}"]
        body.append(f"\\textbf{{Playwright Test:}} {self.process_text(test_name)} \\\\")
        if notes_text:
            body.append(f"\\textbf{{Test Notes:}} {self.process_text(notes_text)} \\\\")

        if os.path.exists(video_path):
            video_name = os.path.basename(video_path)
            body.append(f"\\textbf{{Video recording:}} {self.process_text(video_name)} \\\\")
        else:
            body.append("\\textbf{Video recording:} Not captured \\\\")

        if grid_basename:
            body.append(
                "\\begin{figure}[h]\n"
                "\\centering\n"
                f"\\includegraphics[width=\\linewidth]{{{grid_basename}}}\n"
                f"\\caption{{Key frames captured from {self.process_text(test_name)}}}\n"
                "\\end{figure}"
            )
        else:
            body.append("\\textit{Key frames not available for this run.}")

        body.append(
            "\\vspace{0.2cm}\n"
            "\\begin{lstlisting}[language=bash, basicstyle=\\ttfamily\\small, "
            "breaklines=true, breakatwhitespace=true, "
            "showstringspaces=false, escapeinside={(*}{*)}]\n"
            + log_output
            + "\n"
            + "\\end{lstlisting}"
        )

        return "\n".join(body)

    def _execute_playwright_test(self, test_name: str) -> tuple[str, str, int]:
        if not self.playwright_dir:
            raise RuntimeError(
                "autoplaywright tests require --playwright-dir to be set so Playwright artifacts can be produced."
            )
        script_path = os.path.join(self.playwright_dir, "run-test-video.sh")
        if not os.path.isfile(script_path):
            raise FileNotFoundError(f"Playwright runner script not found: {script_path}")

        video_output = os.path.join(os.getcwd(), self.get_temporary_filename(suffix=".webm"))
        log_output = os.path.join(os.getcwd(), self.get_temporary_filename(suffix=".txt"))

        command = ["bash", script_path, test_name, video_output, log_output]
        try:
            subprocess.run(command, cwd=self.playwright_dir, check=True)  # noqa S603
            exit_code = 0
        except subprocess.CalledProcessError as exc:
            exit_code = exc.returncode

        return video_output, log_output, exit_code

    def _read_playwright_log(self, log_path: str) -> str:
        if not os.path.exists(log_path):
            return "No Playwright output captured."
        with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
            log_content = log_file.read()
        sanitized = log_content.encode("ascii", "backslashreplace").decode("ascii")
        if len(sanitized) > 40000:
            return sanitized[:40000] + " ... [truncated]"
        return sanitized

    def _build_playwright_grid(self, video_path: str) -> Optional[str]:
        if not os.path.exists(video_path):
            return None

        ffmpeg_path, ffprobe_path = self._ensure_ffmpeg_available()
        timestamps = self._select_keyframe_timestamps(video_path, ffprobe_path)
        if not timestamps:
            return None

        frame_paths: list[str] = []
        try:
            for ts in timestamps:
                frame_output = os.path.join(
                    os.getcwd(), self.get_temporary_filename(suffix=".png", force_random=True)
                )
                command = [
                    ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{ts:.3f}", "-i", video_path, "-frames:v", "1", frame_output
                ]
                try:
                    subprocess.run(command, check=True)  # noqa S603
                    if os.path.exists(frame_output):
                        frame_paths.append(frame_output)
                except subprocess.CalledProcessError:
                    continue

            if not frame_paths:
                return None

            collage_path = os.path.join(
                os.getcwd(), self.get_temporary_filename(suffix=".png", force_random=True)
            )
            self._compose_frame_grid(frame_paths, collage_path)
            if os.path.exists(collage_path):
                return os.path.basename(collage_path)

            return None
        finally:
            for frame_path in frame_paths:
                try:
                    os.remove(frame_path)
                except OSError:
                    pass

    def _compose_frame_grid(self, frame_paths: list[str], collage_path: str) -> None:
        images = [Image.open(path).convert("RGB") for path in frame_paths]
        if not images:
            return

        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = getattr(Image, "LANCZOS", 1)

        base_width, base_height = images[0].size
        scale = min(1.0, 360 / base_width) if base_width else 1.0
        target_width = max(1, int(base_width * scale))
        target_height = max(1, int(base_height * scale))
        resized = [img.resize((target_width, target_height), resample=resample) for img in images]

        grid_size = 3
        grid_image = Image.new("RGB", (target_width * grid_size, target_height * grid_size), (20, 20, 20))

        for position in range(grid_size * grid_size):
            row = position // grid_size
            col = position % grid_size
            x = col * target_width
            y = row * target_height
            if position < len(resized):
                grid_image.paste(resized[position], (x, y))

        grid_image.save(collage_path, format="PNG")

    def _select_keyframe_timestamps(self, video_path: str, ffprobe_path: str) -> list[float]:
        duration = self._get_video_duration(video_path, ffprobe_path)
        if duration <= 0:
            return [0.0]

        timestamps: list[float] = []
        for index in range(PLAYWRIGHT_MAX_FRAMES):
            if PLAYWRIGHT_MAX_FRAMES == 1:
                ts = 0.0
            else:
                ts = duration * index / (PLAYWRIGHT_MAX_FRAMES - 1)
            timestamps.append(ts)

        unique_timestamps: list[float] = []
        for ts in timestamps:
            if not unique_timestamps or abs(ts - unique_timestamps[-1]) > 1e-6:
                unique_timestamps.append(ts)
        return unique_timestamps

    def _get_video_duration(self, video_path: str, ffprobe_path: str) -> float:
        try:
            completed = subprocess.run(  # noqa S603
                [
                    ffprobe_path,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Failed to read video duration: {exc}") from exc

        try:
            return max(0.0, float(completed.stdout.strip()))
        except ValueError:
            return 0.0

    def _ensure_ffmpeg_available(self) -> tuple[str, str]:
        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")
        missing = []
        if not ffmpeg_path:
            missing.append("ffmpeg")
        if not ffprobe_path:
            missing.append("ffprobe")
        if missing:
            raise RuntimeError(
                "The autoplaywright feature requires ffmpeg/ffprobe but the following binaries were not found: "
                + ", ".join(missing)
            )
        assert ffmpeg_path is not None and ffprobe_path is not None
        return ffmpeg_path, ffprobe_path

    @staticmethod
    def get_global_tex_vars() -> dict[str, str]:
        return {"version": __version__}

    @staticmethod
    def get_temporary_filename(suffix: str = ".temp", force_random: bool = False) -> str:
        if os.getenv("TRACEFLOW_TESTING") and not force_random:
            return "test" + suffix
        return "".join(random.choices(string.ascii_uppercase, k=10)) + suffix # noqa S311

    def __init__(
        self,
        document: Document,
        *,
        playwright_dir: Optional[str] = None,
        top_left_logo_path: Optional[str] = None,
        top_right_logo_path: Optional[str] = None,
    ):
        self.document = document
        self.original_working_directory = os.getcwd()
        self.top_left_logo_path = top_left_logo_path
        self.top_right_logo_path = top_right_logo_path
        self.playwright_dir: Optional[str] = None
        if playwright_dir:
            resolved_playwright_dir = os.path.abspath(playwright_dir)
            if not os.path.isdir(resolved_playwright_dir):
                raise FileNotFoundError(f"Playwright directory does not exist: {resolved_playwright_dir}")
            self.playwright_dir = resolved_playwright_dir

        # Build a set containing all the test and requiremnt IDs
        self.unique_ids = set()
        for test_page in self.document.tests:
            for test in test_page.items:
                self.unique_ids.add(test.test_id)
        for req_page in self.document.requirements:
            for requirement in req_page.items:
                self.unique_ids.add(requirement.req_id)

    @staticmethod
    def _logo_basename(path: Optional[str], default_name: str) -> str:
        if path:
            return os.path.basename(path)
        return default_name

    def _ensure_logo(
        self,
        *,
        filename: str,
        resource_name: str,
        provided_path: Optional[str],
    ) -> None:
        destination = os.path.join(os.getcwd(), filename)
        if provided_path:
            if not os.path.isfile(provided_path):
                raise FileNotFoundError(f"Logo path does not exist: {provided_path}")
            shutil.copy(provided_path, destination)
        else:
            with open(destination, "wb") as f:
                f.write(load_resource("traceflow.res", resource_name))

    def render(self) -> bytes:

        header = _latex_jinja2_env.from_string(
            load_resource("traceflow.res", "report-header.tex").decode("utf-8")
        )

        tex_vars = self.get_global_tex_vars()
        tex_vars["report_title"] = self.process_text(
            self.document.name + " " + self.document.version + ": Validation Pack"
        )
        top_left_logo_name = PdfReport._logo_basename(self.top_left_logo_path, "traceflow-logo.png")
        top_right_logo_name = PdfReport._logo_basename(self.top_right_logo_path, "voxelflow-logo.png")

        tex_vars["top_left_logo"] = top_left_logo_name
        tex_vars["top_right_logo"] = top_right_logo_name
        document = header.render(**tex_vars)

        # Create the "report" directory if it doesn't exist
        if not os.path.exists("report"):
            os.mkdir("report")

        with isolated_filesystem("report"):

            for req_page in self.document.requirements:
                document += "\\section{" + req_page.title + "}\\label{" + req_page.title + "}\n\n"
                document += self.md_to_latex(req_page.generic_content)

                document += self.build_traceability_matrix(req_page)

                for requirement in req_page.items:
                    document += "\\subsection{" + self.process_text(requirement.req_id + ": " + requirement.title) + "}"
                    document += "\\label{" + requirement.req_id + "}\n\n"

                    linked_test_content: str = ""
                    for test_id_linked in requirement.test_ids:
                        linked_test_content += "**Test ID:** " + test_id_linked + "\n"
                    if linked_test_content != "":
                        document += self.md_to_latex(parse_markdown(linked_test_content))
                        document += "\n\n"

                    document += self.md_to_latex(requirement.content)
                    document += "\n\n"

                document += "\\newpage\n\n"

            for test_page in self.document.tests:
                document += "\\section{" + test_page.title + "}\\label{" + test_page.title + "}\n\n"
                document += self.md_to_latex(test_page.generic_content)

                for test in test_page.items:
                    document += "\\subsection{" + self.process_text(test.test_id + ": " + test.title) + "}"
                    document += "\\label{" + test.test_id + "}\n\n"
                    document += self.md_to_latex(test.content)
                    document += "\n\n"

                document += "\\newpage\n\n"

            document += r"%%%%%%%%%%% END DOCUMENT" + "\n\n" r"\label{LastPage}" + "\n\n" + r"\end{document}" + "\n"

            self._ensure_logo(
                filename=top_left_logo_name,
                resource_name="traceflow-logo.png",
                provided_path=self.top_left_logo_path,
            )
            self._ensure_logo(
                filename=top_right_logo_name,
                resource_name="voxelflow-logo.png",
                provided_path=self.top_right_logo_path,
            )

            # Recursively copy all files from the document.input_dir folder to the current folder
            for root, _, files in os.walk(self.document.input_dir):
                for filename in files:
                    shutil.copy(os.path.join(root, filename), filename)

            output_filename = self.get_temporary_filename(suffix=".tex")

            with open(output_filename, "w") as output_file:
                output_file.write(document)

            pdflatex_command = f"pdflatex -interaction=nonstopmode -halt-on-error {output_filename}"
            try:
                subprocess.check_output(
                    pdflatex_command,
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    errors="replace",
                )  # nopep8
                subprocess.check_output(
                    pdflatex_command,
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    errors="replace",
                )  # nopep8
                subprocess.check_output(
                    pdflatex_command,
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    errors="replace",
                )  # nopep8
            except subprocess.CalledProcessError as exc:
                print("Status : FAIL", exc.returncode, exc.output)
                raise exc

            output_pdf = os.path.splitext(output_filename)[0] + ".pdf"

            with open(output_pdf, "rb") as out:
                return out.read()
