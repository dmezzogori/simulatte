"""Tests for IdentifiableMixin."""

from __future__ import annotations

from simulatte.utils import IdentifiableMixin


class TestIdentifiableMixin:
    """Tests for IdentifiableMixin ID assignment behavior."""

    def test_first_instance_gets_id_zero(self) -> None:
        """First instance of a class should get ID 0."""

        class MyClass(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        obj = MyClass()
        assert obj.id == 0

    def test_sequential_id_assignment(self) -> None:
        """Multiple instances should get sequential IDs."""

        class MyClass(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        obj1 = MyClass()
        obj2 = MyClass()
        obj3 = MyClass()

        assert obj1.id == 0
        assert obj2.id == 1
        assert obj3.id == 2

    def test_different_classes_independent_ids(self) -> None:
        """Different classes should have independent ID counters."""

        class ClassA(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        class ClassB(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        a1 = ClassA()
        b1 = ClassB()
        a2 = ClassA()
        b2 = ClassB()

        assert a1.id == 0
        assert a2.id == 1
        assert b1.id == 0
        assert b2.id == 1

    def test_str_representation(self) -> None:
        """String representation should include class name and ID."""

        class MyClass(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        obj = MyClass()
        assert str(obj) == "MyClass[0]"

    def test_clear_resets_counters(self) -> None:
        """IdentifiableMixin.clear() should reset ID counters."""

        class MyClass(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        MyClass()
        MyClass()
        IdentifiableMixin.clear()

        obj = MyClass()
        assert obj.id == 0

    def test_str_representation_multiple_instances(self) -> None:
        """String representation should show correct ID for each instance."""

        class MyClass(IdentifiableMixin):
            def __init__(self) -> None:
                super().__init__()

        obj1 = MyClass()
        obj2 = MyClass()

        assert str(obj1) == "MyClass[0]"
        assert str(obj2) == "MyClass[1]"
