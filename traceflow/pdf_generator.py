import os
import subprocess  # noqa S404
import tempfile
from contextlib import contextmanager
import shutil
from pkgutil import get_data
import random
import string
from typing import Generator, Optional

import latex.jinja2
import cairosvg

from traceflow.parser import Document
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


def process_text(text: str) -> str:
    return text.replace(r"&", r"\&").replace(r"_", r"\_")


def md_to_latex(items: list[dict]) -> str:

    def handle_paragraph(item: dict) -> str:
        latex = ["\n"]
        for child in item["children"]:
            if child["type"] == "text":
                latex.append(process_text(child["text"]))
            else:
                latex.append(handle_item(child))
        return "".join(latex)

    def handle_heading(item: dict) -> str:
        level = item["level"]
        return "\\" + "sub" * (level - 1) + "section{" + process_text(item["children"][0]["text"]) + "}"

    def handle_list(item: dict) -> str:
        latex = ["\\begin{itemize}"]
        for list_item in item["children"]:
            latex.append("\n\\item ")
            for child in list_item["children"]:
                if child["type"] == "text":
                    latex.append(process_text(child["text"]))
                if child["type"] == "block_text":
                    try:
                        latex.append(process_text(child["children"][0]["text"]))
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
            f'\n\\caption{{{process_text(item["alt"])}}}',
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
        return handle_code(item)

    def handle_manual_test(_: dict) -> str:
        pass_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
        fail_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
        skip_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
        comment_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
        return r"""  # noqa E501
\noindent
\begin{Form}
\textbf{Pass} \CheckBox[name=""" + pass_id + r"""]{} \hspace{2cm} \textbf{Fail} \CheckBox[name=""" + fail_id + r"""]{} \hspace{2cm} \textbf{Skip} \CheckBox[name=""" + skip_id + r"""]{} \\
\vspace{0.2cm}
\textbf{Comments} \\
\TextField[name=""" + comment_id + r""", multiline=true, width=\linewidth, height=2cm]{}
\end{Form}
        """

    def handle_code(item: dict) -> str:
        language = None
        if "info" in item:
            language = item["info"]
        code_content = item["text"]

        if language:
            return f"\\begin{{lstlisting}}[language={language}]\n{code_content}\n\\end{{lstlisting}}"
        return f"\\begin{{lstlisting}}\n{code_content}\n\\end{{lstlisting}}"

    def handle_mermaid(item: dict) -> str:
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as svg_file:
            svg_path = svg_file.name

        mermaid_code = item["text"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".mmd", delete=False) as mmd_file:
            mmd_file.write(mermaid_code)
            mmd_path = mmd_file.name

        subprocess.run(["mmdc", "-i", mmd_path, "-o", svg_path], check=True)  # noqa S607, S603

        os.remove(mmd_path)

        # Convert the SVG to PDF
        pdf_path = svg_path.replace(".svg", ".pdf")
        cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
        os.remove(svg_path)
        return handle_image({"src": pdf_path, "alt": "", "title": "", "type": "image"})

    def handle_item(item: dict) -> str:
        handlers = {
            "paragraph": handle_paragraph,
            "heading": handle_heading,
            "list": handle_list,
            "image": handle_image,
            "block_code": handle_block_code,
            "blank_line": lambda _: "\n",
            "strong": lambda item: f"\\textbf{{{process_text(item['children'][0]['text'])}}}",
            "softbreak": lambda _: "\n",
            "codespan": lambda item: f"\\texttt{{{process_text(item['text'])}}}",
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


class PdfReport():
    @staticmethod
    def get_global_tex_vars() -> dict[str, str]:
        return {"version": __version__}

    @staticmethod
    def get_temporary_filename(suffix: str = ".temp") -> str:
        return "".join(random.choices(string.ascii_uppercase, k=10)) + suffix # noqa S311

    def __init__(self, document: Document):
        self.document = document

    def render(self) -> bytes:

        header = _latex_jinja2_env.from_string(
            load_resource("traceflow.res", "report-header.tex").decode("utf-8")
        )

        tex_vars = self.get_global_tex_vars()
        tex_vars["report_title"] = process_text(self.document.name + " " + self.document.version + ": Validation Pack")
        document = header.render(**tex_vars)

        # Create the "report" directory if it doesn't exist
        if not os.path.exists("report"):
            os.mkdir("report")

        with isolated_filesystem("report"):

            for req_page in self.document.requirements:
                document += "\\section{" + req_page.title + "}\n\n"

                for requirement in req_page.items:
                    document += "\\subsection{" + process_text(requirement.req_id + ": " + requirement.title) + "}"
                    document += "\\label{" + requirement.req_id + "}\n\n"
                    document += md_to_latex(requirement.content)
                    document += "\n\n"

                document += "\\newpage\n\n"

            for test_page in self.document.tests:
                document += "\\section{" + test_page.title + "}\n\n"

                for test in test_page.items:
                    document += "\\subsection{" + process_text(test.test_id + ": " + test.title) + "}"
                    document += "\\label{" + test.test_id + "}\n\n"
                    document += md_to_latex(test.content)
                    document += "\n\n"

                document += "\\newpage\n\n"

            document += r"%%%%%%%%%%% END DOCUMENT" + "\n\n" r"\label{LastPage}" + "\n\n" + r"\end{document}" + "\n"

            with open("traceflow-logo.png", "wb") as f:
                f.write(load_resource("traceflow.res", "traceflow-logo.png"))
            with open("voxelflow-logo.png", "wb") as f:
                f.write(load_resource("traceflow.res", "voxelflow-logo.png"))

            # Recursively copy all files from the document.input_dir folder to the current folder
            print("Copying files from", self.document.input_dir, "to", os.getcwd())
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
