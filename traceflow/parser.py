import os
from typing import Union
from dataclasses import dataclass
from pprint import pprint
import mistune


@dataclass
class Requirement:
    req_id: str
    content: list[dict]
    title: str

@dataclass
class Test:
    test_id: str
    content: list[dict]
    title: str
    req_ids: list[str]


@dataclass
class Design:
    design_id: str
    content: list[dict]
    title: str

    def get_referenced_requirement_ids() -> list[str]:
        """ A list of requirement IDs that are present in the description """
        ...
    def get_referenced_test_ids() -> list[str]:
        """ A list of test IDs that are present in the description """
        ...

@dataclass
class Document:
    title: str
    filename: str
    items: list[Union[Requirement, Test]]

def read_file(file_path: str) -> str:
    with open(file_path, "r") as file:
        content = file.read()
    return content

def parse_markdown(content: str) -> list[dict]:
    markdown_parser = mistune.create_markdown(renderer=None)
    return markdown_parser(content)

def process_directory(directory: str) -> list[Document]:
    documents = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".md"):
                print("Parsing file with name: ", file)
                file_path = os.path.join(root, file)
                content = read_file(file_path)
                parsed_content = parse_markdown(content)
                print(type(parsed_content))
                pprint(parsed_content)
                title = extract_title(parsed_content)
                print("Title: ", title)
                print("\n\n")

                if "requirements" in directory:
                    items = extract_requirements(parsed_content)
                elif "tests" in directory:
                    items = extract_tests(parsed_content)
                elif "design" in directory:
                    items = extract_design(parsed_content)
                else:
                    print(
                        f"Directory is: {directory}, unsure what to parse as. Directory should contain"
                        " `requirements`, `tests` or `design`"
                    )
                    continue
                document = Document(title=title, filename=file, items=items)
                documents.append(document)
    return documents

def is_ast_element_heading(elem: dict) -> int:
    """ Returns 0 if NOT a heading, else returns the level"""
    if elem["type"] != "heading":
        return 0
    return elem["attrs"]["level"]

def get_heading_text(elem: dict) -> str:
    return elem["children"][0]["raw"].strip()

def extract_title(parsed_content: list[dict]) -> str:
    # Extract the first L1 heading as the title. If no such heading exists, that's an error.
    if len(parsed_content) == 0:
        raise ValueError("File has no content")
    if is_ast_element_heading(parsed_content[0]) != 1:
        raise ValueError("First element in file is not L1 heading")
    return get_heading_text(parsed_content[0])

def extract_requirements(parsed_content: list[dict]) -> list[Requirement]:
    requirements = []
    # Every L2 heading in the page must be a requirement
    current_req = Requirement(req_id="", content=[], title="")
    has_started = False
    for elem in parsed_content:
        if is_ast_element_heading(elem) == 2:
            has_started = True
            heading_text = get_heading_text(elem)
            current_req.req_id = heading_text.split(" ")[0].replace(":", "")
            current_req.title = heading_text
            if len(current_req.content) > 0:
                requirements.append(current_req)
                current_req = Requirement(req_id="", content=[], title="")
        else:
            if has_started:
                current_req.content.append(elem)

    return requirements

def extract_tests(parsed_content: list[dict]) -> list[Test]:
    tests: list[Test] = []
    test_id = ""
    req_ids: list[str] = []
    description = ""

    for line in parsed_content.split("\n"):
        if line.startswith("##"):
            # Start of a new test
            test_id = line.strip("## ")
        elif line.startswith("**Requirement ID:**"):
            req_ids.append(line.strip("**Requirement ID:** "))
        elif line.startswith("### Test Steps:"):
            description = line.strip("### Test Steps:")

        if test_id and req_ids and description:
            test = Test(test_id=test_id, description=description, req_ids=req_ids)
            tests.append(test)
            test_id = ""
            req_ids = []
            description = ""

    return tests

def extract_design(parsed_content: Union[str, list[str]]) -> list[Design]:
    design_specs: list[Design] = []
    design_id = ""
    description = ""
    referenced_reqs: list[str] = []
    referenced_tests: list[str] = []

    for line in parsed_content.split("\n"):
        if line.startswith("##"):
            design_id = line.strip("## ")
            description = "TODO"

        if design_id and description:
            design = Design(
                design_id=design_id,
                description=description,
                referenced_requirement_ids=referenced_reqs,
                referenced_test_ids=referenced_tests
            )
            design_specs.append(design)
            design_id = ""
            description = ""
            referenced_reqs: list[str] = []
            referenced_tests: list[str] = []
