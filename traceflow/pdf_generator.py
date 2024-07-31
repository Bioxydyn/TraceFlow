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
import cairosvg

from traceflow.parser import Document, RequirementDocument, parse_markdown
from traceflow.version import __version__

_latex_jinja2_env = latex.jinja2.make_env()


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

    @staticmethod
    def get_global_tex_vars() -> dict[str, str]:
        return {"version": __version__}

    @staticmethod
    def get_temporary_filename(suffix: str = ".temp", force_random: bool = False) -> str:
        if os.getenv("TRACEFLOW_TESTING") and not force_random:
            return "test" + suffix
        return "".join(random.choices(string.ascii_uppercase, k=10)) + suffix # noqa S311

    def __init__(self, document: Document):
        self.document = document
        self.original_working_directory = os.getcwd()

        # Build a set containing all the test and requiremnt IDs
        self.unique_ids = set()
        for test_page in self.document.tests:
            for test in test_page.items:
                self.unique_ids.add(test.test_id)
        for req_page in self.document.requirements:
            for requirement in req_page.items:
                self.unique_ids.add(requirement.req_id)

    def render(self) -> bytes:

        header = _latex_jinja2_env.from_string(
            load_resource("traceflow.res", "report-header.tex").decode("utf-8")
        )

        tex_vars = self.get_global_tex_vars()
        tex_vars["report_title"] = self.process_text(
            self.document.name + " " + self.document.version + ": Validation Pack"
        )
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

            with open("traceflow-logo.png", "wb") as f:
                f.write(load_resource("traceflow.res", "traceflow-logo.png"))
            with open("voxelflow-logo.png", "wb") as f:
                f.write(load_resource("traceflow.res", "voxelflow-logo.png"))

            # Recursively copy all files from the document.input_dir folder to the current folder
            for root, _, files in os.walk(self.document.input_dir):
                for filename in files:
                    shutil.copy(os.path.join(root, filename), filename)

            output_filename = self.get_temporary_filename(suffix=".tex")

            with open(output_filename, "w") as output_file:
                output_file.write(document)

            try:
                subprocess.check_output(
                    f"pdflatex -halt-on-error {output_filename}",
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                )  # nopep8
                subprocess.check_output(
                    f"pdflatex -halt-on-error {output_filename}",
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                )  # nopep8
                subprocess.check_output(
                    f"pdflatex -halt-on-error {output_filename}",
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                )  # nopep8
            except subprocess.CalledProcessError as exc:
                print("Status : FAIL", exc.returncode, exc.output)
                raise exc

            output_pdf = os.path.splitext(output_filename)[0] + ".pdf"

            with open(output_pdf, "rb") as out:
                return out.read()
