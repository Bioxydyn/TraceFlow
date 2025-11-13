import os
from typing import Union, List, Generic, TypeVar, Callable, Type
from dataclasses import dataclass

import mistune
import yaml


@dataclass
class Requirement:
    req_id: str
    content: list[dict]
    title: str
    test_ids: list[str]


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

    def get_referenced_requirement_ids(self) -> list[str]:
        """ A list of requirement IDs that are present in the description """
        raise NotImplementedError

    def get_referenced_test_ids(self) -> list[str]:
        """ A list of test IDs that are present in the description """
        raise NotImplementedError


T = TypeVar('T', bound=Union['Requirement', 'Test'])
C = TypeVar('C', bound='SubDocument')


@dataclass
class SubDocument(Generic[T]):
    title: str
    generic_content: list[dict]
    filename: str
    items: List[T]

    @staticmethod
    def from_file_impl(cls: Type[C], file_path: str, item_generator: Callable[[list[dict]], list[T]]) -> C:
        content = read_file(file_path)
        parsed_content = parse_markdown(content)
        generic_content = []
        for elem in parsed_content:
            if is_ast_element_heading(elem) == 2:
                break
            if not is_ast_element_heading(elem) == 1:
                generic_content.append(elem)
        title = extract_title(parsed_content)
        items = item_generator(parsed_content)

        return cls(title=title, generic_content=generic_content, filename=file_path, items=items)


class RequirementDocument(SubDocument[Requirement]):
    @staticmethod
    def item_generator(parsed_content: list[dict]) -> list[Requirement]:
        requirements: list[Requirement] = []
        # Every L2 heading in the page must be a requirement
        current_req = Requirement(req_id="", content=[], title="", test_ids=[])
        for elem in parsed_content:
            if is_ast_element_heading(elem) == 2:
                if len(current_req.content) > 0:
                    requirements.append(current_req)
                    current_req = Requirement(req_id="", content=[], title="", test_ids=[])
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
        return SubDocument.from_file_impl(RequirementDocument, file_path, RequirementDocument.item_generator)


class TestDocument(SubDocument[Test]):

    @staticmethod
    def extract_requirement_ids(content_item: dict) -> list[str]:
        if content_item["type"] != "paragraph":
            return []
        children = content_item["children"]
        requirements = []
        for index in range(len(children)):
            child = children[index]

            if child["type"] == "strong":
                if child["children"][0]["text"] == "Requirement ID:":
                    # The text of the next element is the requirement ID
                    # Check that we have index +1
                    if index + 1 >= len(children):
                        print("Warning, Requirement ID not found, expcted one after `Requirement ID:`")
                    else:
                        full_req = children[index + 1]["text"].strip().split()[0].split(":")[0]
                        requirements.append(full_req)
        return requirements

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
                    current_test.req_ids += TestDocument.extract_requirement_ids(elem)
        if len(current_test.content) > 0:
            tests.append(current_test)
        return tests

    @staticmethod
    def from_file(file_path: str) -> 'TestDocument':
        return SubDocument.from_file_impl(TestDocument, file_path, TestDocument.item_generator)


@dataclass
class Document:
    requirements: list[RequirementDocument]
    tests: list[TestDocument]
    name: str = ""
    input_dir: str = ""
    version: str = ""

    def verify_all_ids_unique(self) -> None:
        """ Check if all IDs (both test and requirement) are unique """
        all_ids: list[str] = (
            [req.req_id for r in self.requirements for req in r.items]
            + [test.test_id for t in self.tests for test in t.items]
        )

        if len(all_ids) != len(set(all_ids)):
            # There are duplicates - what are they?
            duplicates = [i for i in all_ids if all_ids.count(i) > 1]
            raise ValueError(f"Duplicate IDs found: {duplicates}")

    def build_traceability_matrices(self) -> None:
        for requirement_doc in self.requirements:
            for r in requirement_doc.items:
                r_id = r.req_id
                r.test_ids = []
                for test_doc in self.tests:
                    for t in test_doc.items:
                        if r_id in t.req_ids:
                            r.test_ids.append(t.test_id)

        all_requirement_ids: set[str] = {item.req_id for r in self.requirements for item in r.items}

        # Iterate over the test documents and verify that any referenced requirements exist
        for test_doc in self.tests:
            for t in test_doc.items:
                for req_id in t.req_ids:
                    if req_id not in all_requirement_ids:
                        raise ValueError(f"Test `{t.test_id}` references requirement `{req_id}` which does not exist")

    def __post_init__(self) -> None:
        self.verify_all_ids_unique()
        self.build_traceability_matrices()


def read_file(file_path: str) -> str:
    with open(file_path, "r") as file:
        return file.read()


def parse_markdown(content: str) -> list[dict]:
    parser = mistune.create_markdown(renderer=mistune.AstRenderer(), plugins=['table', 'strikethrough'])
    return parser(content)


def process_directory(directory: str, version: str) -> Document:
    requirements = []
    tests = []
    for root, _, files in os.walk(directory):
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
    return Document(requirements=requirements, tests=tests, name=name, input_dir=absolute_dir_path, version=version)


def is_ast_element_heading(elem: dict) -> int:
    """ Returns 0 if NOT a heading, else returns the level"""
    if elem["type"] != "heading":
        return 0
    return elem["level"]


def get_heading_text(elem: dict) -> str:
    text: str = ""
    for child in elem["children"]:
        if child["type"] == "text":
            text += child["text"]
        if child["type"] == "codespan":
            text += f" `{child['text']}` "
    return text.strip()


def extract_title(parsed_content: list[dict]) -> str:
    # Extract the first L1 heading as the title. If no such heading exists, that's an error.
    if len(parsed_content) == 0:
        raise ValueError("File has no content")
    if is_ast_element_heading(parsed_content[0]) != 1:
        raise ValueError("First element in file is not L1 heading")
    return get_heading_text(parsed_content[0])
