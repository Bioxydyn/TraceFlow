import unittest


class TestHelloWorld(unittest.TestCase):
    def test_zero(self) -> None:
        print("Hello world!")
        self.assertTrue(True)
