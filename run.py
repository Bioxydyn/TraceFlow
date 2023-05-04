from traceflow.parser import process_directory
if __name__ == "__main__":
    requirements = process_directory("examples/requirements")
    design = process_directory("examples/design")
    tests = process_directory("examples/tests")

    print("Parsed requirements documents:")
    for doc in requirements:
        print(doc)

    print("Parsed design documents:")
    for doc in design:
        print(doc)

    print("Parsed test plan documents:")
    for doc in tests:
        print(doc)
