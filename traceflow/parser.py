import os
from typing import Union, List, Generic, TypeVar, Callable
from dataclasses import dataclass
from pprint import pprint

import mistune
import yaml


@dataclass
class Requirement:
    req_id: str
    content: list[dict]
    title: str

    def print(self) -> str:
        print(f"Requirement {self.req_id}: {self.title}\n")
        for elem in self.content:
            pprint(elem)
            print("\n")
        print("\n")


@dataclass
class Test:
    test_id: str
    content: list[dict]
    title: str
    req_ids: list[str]

    def print(self) -> str:
        print(f"Test {self.test_id}: {self.title}\n")
        for elem in self.content:
            pprint(elem)
            print("\n")
        print("\n")


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


T = TypeVar('T', bound=Union['Requirement', 'Test'])


@dataclass
class SubDocument(Generic[T]):
    title: str
    filename: str
    items: List[T]

    def print(self) -> None:
        print(f"Document {self.title} ({self.filename})\n")
        for item in self.items:
            print(item)

    @staticmethod
    def from_file(file_path: str, item_generator: Callable[list[dict], list[T]]) -> 'SubDocument[T]':
        content = read_file(file_path)
        parsed_content = parse_markdown(content)
        title = extract_title(parsed_content)
        items = item_generator(parsed_content)
        return SubDocument(title=title, filename=file_path, items=items)

class RequirementDocument(SubDocument[Requirement]):
    @staticmethod
    def item_generator(parsed_content: list[dict]) -> list[Requirement]:
        requirements: list[Requirement] = []
        # Every L2 heading in the page must be a requirement
        current_req = Requirement(req_id="", content=[], title="")
        for elem in parsed_content:
            if is_ast_element_heading(elem) == 2:
                if len(current_req.content) > 0:
                    requirements.append(current_req)
                    current_req = Requirement(req_id="", content=[], title="")
                heading_text = get_heading_text(elem)
                current_req.req_id = heading_text.split(" ")[0].replace(":", "")
                current_req.title = heading_text.replace(current_req.req_id + ":", "").strip()
            else:
                if current_req.req_id != "":
                    current_req.content.append(elem)
        if len(current_req.content) > 0:
            requirements.append(current_req)
        return requirements

    @staticmethod
    def from_file(file_path: str) -> 'RequirementDocument':
        return SubDocument.from_file(file_path, RequirementDocument.item_generator)


class TestDocument(SubDocument[Test]):
    @staticmethod
    def item_generator(parsed_content: list[dict]) -> list[Test]:
        tests: list[Test] = []
        current_test = Test(test_id="", content=[], title="", req_ids=[])
        for elem in parsed_content:
            if is_ast_element_heading(elem) == 2:
                if len(current_test.content) > 0:
                    tests.append(current_test)
                    current_test = Test(test_id="", content=[], title="", req_ids=[])
                heading_text = get_heading_text(elem)
                current_test.test_id = heading_text.split(" ")[0].replace(":", "")
                current_test.title = heading_text.replace(current_test.test_id + ":", "").strip()
            else:
                if current_test.test_id != "":
                    current_test.content.append(elem)
        if len(current_test.content) > 0:
            tests.append(current_test)
        return tests

    @staticmethod
    def from_file(file_path: str) -> 'TestDocument':
        return SubDocument.from_file(file_path, TestDocument.item_generator)


@dataclass
class Document:
    requirements: list[RequirementDocument]
    tests: list[TestDocument]
    name: str = ""
    input_dir: str = ""
    version: str = ""


def read_file(file_path: str) -> str:
    with open(file_path, "r") as file:
        content = file.read()
    return content


def parse_markdown(content: str) -> list[dict]:
    markdown_parser = mistune.create_markdown(renderer=mistune.AstRenderer())
    return markdown_parser(content)


def process_directory(directory: str, version: str) -> Document:
    requirements = []
    tests = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                if "req" in file_path.lower():
                    requirements.append(RequirementDocument.from_file(file_path))
                elif "test" in file_path.lower():
                    tests.append(TestDocument.from_file(file_path))
                elif "design" in file_path.lower():
                    print("Warning, skipping design parsing (not implemented)")
                else:
                    print(
                        f"Directory is: {directory}, unsure what to parse as. Directory should contain"
                        " `requirements`, `tests` or `design`"
                    )
                    continue
    absolute_dir_path = os.path.abspath(directory)

    # Check if config.yml exists in the directory
    config_file_path = os.path.join(absolute_dir_path, "config.yml")
    name = directory
    if os.path.exists(config_file_path):
        with open(config_file_path, "r") as config_file:
            config = yaml.safe_load(config_file)
            if "name" in config:
                name = config["name"]
                print(f"Found name in config.yml: {name}")
    return Document(requirements=requirements, tests=tests, name=name, input_dir=absolute_dir_path, version=version)


def is_ast_element_heading(elem: dict) -> int:
    """ Returns 0 if NOT a heading, else returns the level"""
    if elem["type"] != "heading":
        return 0
    return elem["level"]


def get_heading_text(elem: dict) -> str:
    return elem["children"][0]["text"].strip()


def extract_title(parsed_content: list[dict]) -> str:
    # Extract the first L1 heading as the title. If no such heading exists, that's an error.
    if len(parsed_content) == 0:
        raise ValueError("File has no content")
    if is_ast_element_heading(parsed_content[0]) != 1:
        raise ValueError("First element in file is not L1 heading")
    return get_heading_text(parsed_content[0])
