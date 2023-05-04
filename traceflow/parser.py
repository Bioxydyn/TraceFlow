import os
from typing import Union
from dataclasses import dataclass

import mistune

@dataclass
class Requirement:
    req_id: str
    description: str

@dataclass
class Test:
    test_id: str
    description: str
    req_ids: list[str]

@dataclass
class Design:
    design_id: str
    description: str
    referenced_requirement_ids: list[str]  # A list of requirement IDs that are present in the description
    referenced_test_ids: list[str]  # A list of test IDs that are present in the description

@dataclass
class Document:
    title: str
    filename: str
    items: list[Union[Requirement, Test]]

def read_file(file_path: str) -> str:
    with open(file_path, "r") as file:
        content = file.read()
    return content

def parse_markdown(content: str) -> Union[str, list[str]]:
    markdown_parser = mistune.create_markdown()
    parsed_content = markdown_parser(content)
    return parsed_content

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
                if "requirements" in directory:
                    items = extract_requirements(parsed_content)
                elif "tests" in directory:
                    items = extract_tests(parsed_content)
                elif "design" in directory:
                    items = extract_design(parsed_content)
                else:
                    print(f"Directory is: {directory}, unsure what to parse as. Directory should contain `requirements`, `tests` or `design`")
                    continue
                title = extract_title(parsed_content)
                document = Document(title=title, filename=file, items=items)
                documents.append(document)
    return documents

def extract_title(parsed_content: Union[str, list[str]]) -> str:
    # Extract the first line as the title
    title = parsed_content.split("\n")[0].strip("# ")
    return title

def extract_requirements(parsed_content: Union[str, list[str]]) -> list[Requirement]:
    requirements = []
    for line in parsed_content.split("\n"):
        if line.startswith("##"):
            req_id, description = line.strip("## ").split(": ", 1)
            requirement = Requirement(req_id=req_id, description=description)
            requirements.append(requirement)
    return requirements

def extract_tests(parsed_content: Union[str, list[str]]) -> list[Test]:
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
