"""
Parses dependency types for use in HeaderQuery
"""

from enum import Enum, auto


class DependencyType(Enum):
    UNSPECIFIED = auto()
    STRUCT_SPECIFIER = auto()
    ENUM_SPECIFIER = auto()

    @property
    def prefix(self) -> str:
        match self:
            case DependencyType.ENUM_SPECIFIER:
                return "enum"
            case DependencyType.UNSPECIFIED:
                return "struct"
            case DependencyType.STRUCT_SPECIFIER:
                return "struct"

    @staticmethod
    def from_str(t: str):
        """
        Return the enum representing the given dependency type

        :param t: a string representing the type
        :return: the enum representing this type
        """
        match t:
            case "enum_specifier":
                return DependencyType.ENUM_SPECIFIER
            case "struct_specifier":
                return DependencyType.STRUCT_SPECIFIER
            case _:
                return DependencyType.UNSPECIFIED


class Dependency:
    _type: DependencyType
    _name: str

    def __init__(self, t: DependencyType, name: str):
        self._type = t
        self._name = name

    @property
    def type(self) -> DependencyType:
        return self._type

    @property
    def name(self) -> str:
        return self._name
