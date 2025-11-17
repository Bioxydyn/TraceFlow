import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union, List, Generic, TypeVar, Callable, Type

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
class Risk:
    risk_id: str
    content: list[dict]
    title: str
    attributes: dict[str, str]
    requirement_refs: list[str]
    test_refs: list[str]


@dataclass
class MarkdownDocument:
    title: str
    filename: str
    content: list[dict]
    category: str

    @staticmethod
    def from_file(file_path: str, category: str) -> 'MarkdownDocument':
        parsed = parse_markdown(read_file(file_path))
        title = extract_title(parsed)
        content = parsed[1:]
        return MarkdownDocument(title=title, filename=file_path, content=content, category=category)


T = TypeVar('T', bound=Union['Requirement', 'Test', 'Risk'])
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


class RiskDocument(SubDocument[Risk]):

    _FIELD_ALIASES: dict[str, str] = {
        "hazardous situation": "hazardous_situation",
        "hazard": "hazardous_situation",
        "harm": "harm",
        "cause": "cause",
        "severity": "severity",
        "probability": "probability",
        "risk estimate": "risk_estimate",
        "risk estimation": "risk_estimate",
        "controls": "controls",
        "risk control": "controls",
        "risk controls": "controls",
        "risk control measure": "controls",
        "residual risk": "residual_risk",
        "residual risk assessment": "residual_risk",
        "residual severity": "residual_severity",
        "residual probability": "residual_probability",
        "detection": "detection",
        "linked requirement": "linked_requirements",
        "linked requirements": "linked_requirements",
        "linked test": "linked_tests",
        "linked tests": "linked_tests",
    }

    _REQ_PATTERN = re.compile(r"\bREQ-[A-Za-z0-9_-]+\b", re.IGNORECASE)
    _TEST_PATTERN = re.compile(r"\bTEST-[A-Za-z0-9_-]+\b", re.IGNORECASE)

    @staticmethod
    def _normalise_field_name(raw: str) -> str | None:
        normalised = re.sub(r"[^a-z0-9 ]", "", raw.lower().strip())
        return RiskDocument._FIELD_ALIASES.get(normalised)

    @staticmethod
    def _flatten_text(elem: dict) -> str:
        if elem["type"] == "text":
            return elem.get("text", "")
        if elem["type"] == "codespan":
            return elem.get("text", "")

        if elem["type"] in {"softbreak", "linebreak"}:
            return "\n"

        text = ""
        for child in elem.get("children", []):
            text += RiskDocument._flatten_text(child)
        return text

    @staticmethod
    def _extract_field(elem: dict) -> tuple[str, str] | None:
        if elem["type"] != "paragraph":
            return None
        text = RiskDocument._flatten_text(elem).strip()
        if ":" not in text:
            return None
        field_name, value = text.split(":", 1)
        canonical = RiskDocument._normalise_field_name(field_name)
        if canonical is None:
            return None
        return canonical, value.strip()

    @staticmethod
    def _extract_references(text: str) -> tuple[list[str], list[str]]:
        reqs = [match.upper() for match in RiskDocument._REQ_PATTERN.findall(text)]
        tests = [match.upper() for match in RiskDocument._TEST_PATTERN.findall(text)]
        return reqs, tests

    @staticmethod
    def item_generator(parsed_content: list[dict]) -> list[Risk]:
        risks: list[Risk] = []
        current_risk = Risk(
            risk_id="",
            content=[],
            title="",
            attributes={},
            requirement_refs=[],
            test_refs=[],
        )
        for elem in parsed_content:
            if is_ast_element_heading(elem) == 2:
                if len(current_risk.content) > 0:
                    risks.append(current_risk)
                    current_risk = Risk(
                        risk_id="",
                        content=[],
                        title="",
                        attributes={},
                        requirement_refs=[],
                        test_refs=[],
                    )
                heading_text = get_heading_text(elem)
                current_risk.risk_id = heading_text.split(" ")[0].replace(":", "")
                current_risk.title = heading_text.replace(current_risk.risk_id + ":", "").strip()
            else:
                if current_risk.risk_id != "":
                    current_risk.content.append(elem)
                    extracted_field = RiskDocument._extract_field(elem)
                    flattened = RiskDocument._flatten_text(elem)
                    req_refs, test_refs = RiskDocument._extract_references(flattened)
                    for req in req_refs:
                        if req not in current_risk.requirement_refs:
                            current_risk.requirement_refs.append(req)
                    for test in test_refs:
                        if test not in current_risk.test_refs:
                            current_risk.test_refs.append(test)
                    if extracted_field:
                        field_name, field_value = extracted_field
                        current_risk.attributes[field_name] = field_value
                        # If the field explicitly lists linked requirements/tests, merge them back in
                        if field_name in {"linked_requirements", "controls"}:
                            reqs_in_field, _ = RiskDocument._extract_references(field_value)
                            for req in reqs_in_field:
                                if req not in current_risk.requirement_refs:
                                    current_risk.requirement_refs.append(req)
                        if field_name in {"linked_tests", "controls"}:
                            _, tests_in_field = RiskDocument._extract_references(field_value)
                            for test in tests_in_field:
                                if test not in current_risk.test_refs:
                                    current_risk.test_refs.append(test)
        if len(current_risk.content) > 0:
            risks.append(current_risk)
        return risks

    @staticmethod
    def from_file(file_path: str) -> 'RiskDocument':
        return SubDocument.from_file_impl(RiskDocument, file_path, RiskDocument.item_generator)


@dataclass
class Document:
    requirements: list[RequirementDocument]
    tests: list[TestDocument]
    risks: list[RiskDocument]
    design_documents: list[MarkdownDocument]
    supplementary_documents: list[MarkdownDocument]
    name: str = ""
    input_dir: str = ""
    version: str = ""

    def verify_all_ids_unique(self) -> None:
        """ Check if all IDs (both test and requirement) are unique """
        all_ids: list[str] = (
            [req.req_id for r in self.requirements for req in r.items]
            + [test.test_id for t in self.tests for test in t.items]
            + [risk.risk_id for risk_doc in self.risks for risk in risk_doc.items]
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

    def verify_risk_references(self) -> None:
        """Ensure that any requirement/test IDs referenced within the risk register exist."""
        known_requirements: set[str] = {item.req_id for doc in self.requirements for item in doc.items}
        known_tests: set[str] = {item.test_id for doc in self.tests for item in doc.items}
        for risk_doc in self.risks:
            for risk in risk_doc.items:
                for req_id in risk.requirement_refs:
                    if req_id not in known_requirements:
                        raise ValueError(
                            f"Risk `{risk.risk_id}` references requirement `{req_id}` which does not exist"
                        )
                for test_id in risk.test_refs:
                    if test_id not in known_tests:
                        raise ValueError(f"Risk `{risk.risk_id}` references test `{test_id}` which does not exist")

    def __post_init__(self) -> None:
        self.verify_all_ids_unique()
        self.build_traceability_matrices()
        self.verify_risk_references()


def read_file(file_path: str) -> str:
    with open(file_path, "r") as file:
        return file.read()


def parse_markdown(content: str) -> list[dict]:
    parser = mistune.create_markdown(renderer=mistune.AstRenderer(), plugins=['table', 'strikethrough'])
    return parser(content)


def process_directory(directory: str, version: str) -> Document:
    requirements = []
    tests = []
    risks = []
    design_documents: list[MarkdownDocument] = []
    supplementary_documents: list[MarkdownDocument] = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".md"):
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, directory)
                path_components = [part.lower() for part in Path(relative_path).parts]
                top_component = path_components[0]
                filename_component = path_components[-1]

                if top_component.startswith("req") or filename_component.startswith("req"):
                    requirements.append(RequirementDocument.from_file(file_path))
                elif top_component.startswith("test") or filename_component.startswith("test"):
                    tests.append(TestDocument.from_file(file_path))
                elif (
                    top_component.startswith("risk")
                    or filename_component.startswith("risk")
                    or top_component.startswith("hazard")
                    or filename_component.startswith("hazard")
                ):
                    risks.append(RiskDocument.from_file(file_path))
                elif top_component.startswith("design") or filename_component.startswith("design"):
                    design_documents.append(MarkdownDocument.from_file(file_path, category="design"))
                else:
                    supplementary_documents.append(MarkdownDocument.from_file(file_path, category="general"))
    absolute_dir_path = os.path.abspath(directory)

    # Check if config.yml exists in the directory
    config_file_path = os.path.join(absolute_dir_path, "config.yml")
    name = directory
    if os.path.exists(config_file_path):
        with open(config_file_path, "r") as config_file:
            config = yaml.safe_load(config_file)
            if "name" in config:
                name = config["name"]
    return Document(
        requirements=requirements,
        tests=tests,
        risks=risks,
        design_documents=design_documents,
        supplementary_documents=supplementary_documents,
        name=name,
        input_dir=absolute_dir_path,
        version=version,
    )


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
