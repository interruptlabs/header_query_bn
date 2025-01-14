"""
Creates nodes from tree-sitter queries and searches for dependent nodes.
"""

from typing import Dict, Set

import binaryninja as bn
from tree_sitter import Language, Node
import tree_sitter_c as tsc

from .dependency import Dependency, DependencyType
from .queries import DEPENDENCY_QUERY


class HeaderQueryNode:
    """
    Holds all information relevant to a function, type or typedef declaration.
    """

    _name: str
    _ts_node: Node
    _type: str
    _c_string: str
    _is_function: bool
    _dependencies: Set[Dependency]
    _bv_func: bn.Function
    _alias: Set[str]

    def __init__(self, ts_node: Node, t: str, c_string: str, name: str):
        self._name = name  # Name of function/type
        self._ts_node = ts_node  # tree-sitter node reference
        self._type = t  # tree-sitter node type
        self._c_string = c_string  # c code for this Function/type
        self._is_function = False
        self._dependencies = (
            set()
        )  # a set of Dependency objects representing the dependencies of this Node.
        self._bv_func = None  # reference to the binary view Function object
        self._alias = set()  # alias name if an alias exists, otherwise empty.

    @property
    def name(self) -> str:
        return self._name

    @property
    def type(self) -> str:
        return self._type

    @property
    def c_string(self) -> str:
        return self._c_string

    @property
    def is_function(self) -> bool:
        return self._is_function

    @is_function.setter
    def is_function(self, isfunc: bool):
        self._is_function = isfunc

    @property
    def dependencies(self) -> Set[Dependency]:
        return self._dependencies

    @property
    def bv_func(self) -> bn.Function:
        return self._bv_func

    @bv_func.setter
    def bv_func(self, bv_func: bn.Function):
        self._bv_func = bv_func

    @property
    def alias(self) -> str | None:
        return self._alias

    @alias.setter
    def alias(self, alias: str):
        self._alias = alias

    def add_dependency(self, dependency: Dependency):
        """Add the given dependency to the dependencies set"""
        self._dependencies.add(dependency)

    @property
    def dependency_names(self) -> Set[str]:
        """
        Get the names of all top-level dependencies of this node.

        :return: A set of names (string) of dependencies of this node.
        """
        return {dep.name for dep in self._dependencies}

    def get_func(self, bv_func_dict: Dict[str, bn.Function]) -> bn.Function:
        """
        Get the binary view function for this node.

        :param bv_func_dict: A dictionary of (name, binary view Function object)
        :return: the binary view function object for this node, or None if it does not exist.
        """
        return bv_func_dict.get(self._name)

    def update_top_level_dependencies(self):
        """
        Query this node for Field and Parameter top-level dependencies.

        :return: a list of Dependency objects found in this tree-sitter node.
        """

        language = Language(tsc.language())
        matches = language.query(DEPENDENCY_QUERY).matches(self._ts_node)

        for _, capture in matches:
            name = ""
            # all valid fields/parameters should have a name capture.
            if "name" in capture:
                name = capture["name"][0].text.decode("utf8")
                if "type" in capture:
                    c = capture["type"][0]
                    self.add_dependency(
                        Dependency(DependencyType.from_str(c.type), name)
                    )
                else:
                    # If no type captured, the specifier isn't present in the declaration
                    self.add_dependency(Dependency(DependencyType.UNSPECIFIED, name))
