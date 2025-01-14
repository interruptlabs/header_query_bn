"""
Binary Ninja plugin for importing types and function parameters from unprocessed (containing pre-processor directives) or incomplete header files.
"""

from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import binaryninja as bn
from tree_sitter import Language, Node, Parser
import tree_sitter_c as tsc

from .dependency import DependencyType, Dependency
from .header_query_node import HeaderQueryNode
from .queries import *


class HeaderQueryPlugin(bn.BackgroundTaskThread):
    """Import types, typedefs and function declarations for functions found in both the header(s) and the binary view."""

    _bv: bn.BinaryView
    _parser: Parser

    def __init__(self, bv: bn.BinaryView):
        super().__init__("Importing headers...", can_cancel=True)

        self._bv = bv
        self._parser = Parser()
        self._parser.language = Language(tsc.language())

    def run(self):
        """
        Import types and rename function parameters for matching functions between the binary view and selected header directory.
        """

        include_dir = select_include_directory()
        if not include_dir:
            bn.log_error("No include directory selected")
            return
        pathlist = include_dir.rglob("*.h")

        overwrite = bn.get_choice_input(
            "Overwrite pre-defined types?",
            "Overwrite all pre-defined types",
            ("No", "Yes", "Select individual"),
        )

        if overwrite is None:
            return  # User selected Cancel

        all_types: Set[HeaderQueryNode] = set()
        all_type_defs: Set[HeaderQueryNode] = set()
        desired_functions: Set[HeaderQueryNode] = set()
        all_nodes: Set[HeaderQueryNode] = set()
        error_nodes: List[Node] = []
        error_filenames: List[str] = []

        bv_func_dict = {f.name: f for f in self._bv.functions}

        # loop through all headers in include directory
        for path in pathlist:
            root = self._parser.parse(path.read_bytes(), encoding="utf8").root_node

            # Search for and create typedefs first for checking
            create_typedef_nodes(root, self._parser.language, all_type_defs, all_nodes)

            local_error_nodes = create_nodes_from_query(
                root,
                self._parser.language,
                QUERY,
                all_nodes,
                desired_functions,
                all_types,
                (0, 1),
                2,
                bv_func_dict,
            )
            if local_error_nodes:
                error_filenames.append(path.name)

            all_types.update(all_type_defs)
            error_nodes.extend(local_error_nodes)

            create_void_function_nodes(
                root,
                self._parser.language,
                FUNCTION_NAME,
                all_nodes,
                desired_functions,
                bv_func_dict,
            )

        dependency_names = find_all_dependencies(desired_functions, all_types)

        if not overwrite:
            # Search bv for types that are already defined.
            undefined_dep_names, _ = self._identify_predefined_types(dependency_names)
            nodes = get_nodes_from_names(undefined_dep_names, all_types)
        elif overwrite == 2:  # Select Individual
            undefined_dep_names, already_defined = self._identify_predefined_types(
                dependency_names
            )
            selections = present_type_selection_form(already_defined)
            nodes = get_nodes_from_names(undefined_dep_names, all_types)
            nodes.update(get_nodes_from_names(selections, all_types))
        else:
            nodes = get_nodes_from_names(dependency_names, all_types)

        with self._bv.undoable_transaction():
            created = self._create_bv_type_stubs(nodes | desired_functions)
            type_fail, undefined = self._create_bv_types(nodes, created)

            # Heuristic for determining whether we reanalyze individual functions or the entire BNDB
            reanalyze_funcs = len(desired_functions) < (0.5 * len(self._bv.functions))
            func_fail, func_success = self.overwrite_bv_func_type(
                desired_functions, reanalyze_funcs
            )

        with self._bv.undoable_transaction():
            self._propagate_variable_names(func_success)

        body = create_report(
            func_fail,
            func_success,
            type_fail,
            undefined,
            error_nodes,
            error_filenames,
        )

        self._bv.show_markdown_report("HeaderQuery Results", body, body)

    def _identify_predefined_types(
        self, dependency_names: Iterable[str]
    ) -> Tuple[Set[str], Set[str]]:
        """
        Identify all types in dependency_names that are already defined in the Binary View (bv).

        :param dependecy_names: A set of names (string) of dependencies to check.
        :return: A tuple containing (1) a set of names of dependencies that are
                 undefined in the bv, and (2) a set of names of dependencies
                 that are already defined in the bv.
        """
        undefined_dep_names = set()
        already_defined = set()

        for name in dependency_names:
            if any(t[0] == name for t in self._bv.types):
                already_defined.add(name)
            else:
                undefined_dep_names.add(name)

        return undefined_dep_names, already_defined

    def _create_bv_type_stubs(
        self,
        nodes: Set[HeaderQueryNode],
    ) -> Set[str]:
        """
        Create blank stubs for all types and dependencies.

        :param nodes: A set of HeaderQueryNodes representing all types
        :return: A set of names (string) of all stubs created.
        """
        created = set()
        for node in nodes:
            for dep in node.dependencies:
                if dep.name not in created:
                    try:
                        c_string = f"{dep.type.prefix} {dep.name} {{}};"
                        t = self._bv.parse_types_from_string(c_string)
                        self._bv.define_user_type(dep.name, t.types.get(dep.name))
                        created.add(dep.name)
                    except Exception as e:
                        bn.log_warn(f"failed to define stub: {dep.name}, {e}")
        return created

    def _create_bv_types(
        self, nodes: Set[HeaderQueryNode], created: Set[str]
    ) -> Tuple[Dict[str, str], Set[str]]:
        """
        Create the BinaryView Type for every type in the list.

        :param types: The list of tuples of (name, node) for types to create.
        :return: A tuple of (Dictionary, Set) containing a Dictionary of failed
                 type names and failure reasons, and a Set of names of successful
                 type creations.
        """
        defined_types = set()
        failures = {}
        unprocessed_types = set()

        for node in nodes:  # Import enums first before structs
            if node.name not in defined_types:
                if node.type == "enum_specifier":
                    c_string = f"{node.c_string};"
                    try:
                        type_parser = self._bv.parse_types_from_string(c_string)
                        bv_types = type_parser.types
                        for key in bv_types:
                            self._bv.define_user_type(key, bv_types[key])
                            defined_types.add(key)
                            created.discard(key)
                    except Exception as e:
                        failures[node.name] = str(e)
                else:
                    unprocessed_types.add(node)

        for node in unprocessed_types:  # Import everything else
            if node.name not in defined_types:
                # only define if not already defined
                try:
                    if node.type == "struct_specifier":
                        c_string = f"{node.c_string};"
                    else:
                        c_string = node.c_string

                    type_parser = self._bv.parse_types_from_string(c_string)
                    bv_types = type_parser.types
                    for key in bv_types:
                        self._bv.define_user_type(key, bv_types[key])
                        defined_types.add(key)
                        created.discard(key)
                except Exception as e:
                    failures[node.name] = str(e)

        bn.log_info(
            f"Defined {len(nodes)} types, failed to define {len(failures)} types"
        )
        return failures, created

    def overwrite_bv_func_type(
        self, nodes: Set[HeaderQueryNode], reanalyze_funcs: bool
    ) -> Tuple[Dict[str, str], List[HeaderQueryNode]]:
        """
        Overwrite the corresponding bv function.type if the function exists.

        :param funcs: List of HeaderQueryNodes representing Functions.
        :return: A dictionary of (name, failure) where failure is the exception
                 string encountered and the name is the name of the function.
        """
        failures = {}
        success = []

        for node in nodes:
            try:
                node.bv_func.type = node.c_string
                success.append(node)
                if reanalyze_funcs:
                    node.bv_func.reanalyze()  # reanalyze this function

            except Exception as e:
                failures[node.name] = str(e)

        if not reanalyze_funcs:
            self._bv.reanalyze()  # reanalyze globally

        bn.log_info(
            f"Redefined {len(nodes)} functions, failed to redefine {len(failures)} functions"
        )
        return failures, success

    def _propagate_variable_names(self, nodes: Set[HeaderQueryNode]):
        """
        Propagate parameter names from annotated functions to their caller functions

        :param nodes: Set of HeaderQueryNodes for functions that were annotated.
        """
        # XXX Occasionally produces non-deterministic results (no variables renamed).
        # Running a second time will usually result in successful renaming.

        def rename_caller(
            caller: bn.binaryview.ReferenceSource,
            param: bn.variable.Variable,
            new_name: str,
        ) -> int:
            caller_params = list(caller.function.parameter_vars)
            count = 0
            change = False

            for j, p in enumerate(caller_params):
                if param == p and p.name.startswith("arg"):
                    # Only rename parameters that have the default `argX` name
                    # Do not rename arguments the user may have already renamed
                    caller_params[j].name = new_name
                    change = True
                    count += 1
            if change:
                # Add callers to worklist if we renamed any parameters
                caller.function.reanalyze()
            return count

        worklist = [node.bv_func for node in nodes]
        count = 0

        while worklist:
            func = worklist.pop(0)
            param_names = [p.name for p in func.parameter_vars]

            # Skip functions with no arguments
            if not param_names:
                continue

            for caller in func.caller_sites:
                # Force Binary Ninja to generate hlil if possible, per
                # https://github.com/Vector35/binaryninja-api/issues/5765
                try:
                    caller.function.hlil
                except bn.ILException:
                    bn.log_error(f"Failed to get hlil for {caller.function.name}")
                    continue

                for operand in caller.hlil.operands:
                    if not isinstance(operand, bn.HighLevelILCall):
                        continue
                    # Look at function calls
                    for i, param in enumerate(operand.params):
                        # XXX Probably need to handle other (simple) cases
                        if isinstance(param, bn.HighLevelILVar):
                            # Rename a variable directly
                            count += rename_caller(caller, param.var, param_names[i])
                        elif isinstance(param, bn.HighLevelILUnaryBase) and isinstance(
                            param.operands[0], bn.HighLevelILVar
                        ):
                            # Rename a variable inside a unary operation
                            count += rename_caller(
                                caller, param.operands[0].var, param_names[i]
                            )
        print(f"renamed {count} variables")


def find_all_dependencies(
    desired_functions: Set[HeaderQueryNode], all_type_nodes: Set[HeaderQueryNode]
) -> Set[str]:
    """
    Find all dependencies for the given functions and types.

    :param desired_functions: A set of HeaderQueryNodes representing desired functions
    :param all_type_nodes: A set of HeaderQueryNodes representing all types
    :return: A set of names (strings) of all dependencies found
    """
    tmp_dependencies = set()
    searched = set()

    # find top-level dependencies for all functions. Return types and parameters.
    for node in desired_functions:
        node.update_top_level_dependencies()
        tmp_dependencies.update(node.dependency_names)

    # search for exhaustive set of dependencies
    while True:
        if tmp_dependencies.issubset(searched):
            break
        unsearched = set()
        for dependency in tmp_dependencies:
            if dependency not in searched:
                unsearched.add(dependency)

        tmp_dependencies = set()
        for node in all_type_nodes:
            if node.name in unsearched or any(
                alias in unsearched for alias in node.alias
            ):
                node.update_top_level_dependencies()
                tmp_dependencies.update(node.dependency_names)
                searched.add(node.name)
                if node.alias:
                    searched.update(node.alias)
        # add all dependencies that were not found in these headers so we can create type stubs
        searched.update(unsearched)

    return searched


def get_nodes_from_names(
    names: Set[str], all_nodes: Set[HeaderQueryNode]
) -> Set[HeaderQueryNode]:
    """
    Get the HeaderQueryNode reference for the given names

    :param names: set of names (string) to search for
    :param all_nodes: set of HeaderQueryNode to search within
    :return: A set of all nodes found.
    """

    return {
        node
        for node in all_nodes
        if node.name in names or any(alias in names for alias in node.alias)
    }


def create_nodes_from_query(
    root: Node,
    language: Language,
    query: str,
    all_nodes: Set[HeaderQueryNode],
    desired_functions: Set[HeaderQueryNode],
    all_types: Set[HeaderQueryNode],
    function_query_index: Iterable[int],
    error_query_index: int,
    bv_func_dict: Dict[str, bn.Function],
) -> List[Node]:
    """
    Query the AST and create HeaderQueryNodes for all functions and types found.

    :param root: the root tree-sitter node.
    :param language: the tree-sitter Language to use.
    :param query: A tree-sitter Query string
    :param all_nodes: A set of all HeaderQueryNodes that have been found so far.
    :param desired_functions: A set of HeaderQueryNodes for functions that also appear in the BinaryView.
    :param all_types: A set of HeaderQueryNodes for types that have been found.
    :param function_query_index: A tuple of indicies of the location of function queries in the query string.
    :return: A list of error nodes.
    """
    name = ""
    error_nodes = []
    matches = language.query(query).matches(root)

    for index, capture in matches:
        if index == error_query_index:
            error_nodes.append(capture["error"][0])
            continue

        ts_node = capture["node"][0]
        if index in function_query_index:
            function_declaration = language.query(FUNCTION_NAME).matches(ts_node)
            if not function_declaration:
                continue  # this match is a non-function declaration

            # Take the first match. We do not handle nested function definitions
            _, c = function_declaration[0]
            name = c["name"][0].text.decode("utf8")
        else:
            name = capture["name"][0].text.decode("utf8")

        if any(node.name == name for node in all_nodes):
            continue
        # if node has not already been found,
        try:
            node = HeaderQueryNode(
                ts_node, ts_node.type, ts_node.text.decode("utf8"), name
            )
            if "return_type" in capture:
                t = capture["return_type"][0]
                # Name of the return_type capture differs based on the type
                if t.type not in ("primitive_type", "sized_type_specifier"):
                    name = t.text.decode("utf8")
                    if t.type in ("struct_specifier", "enum_specifier"):
                        child = t.child(1)
                        assert child.type == "type_identifier"
                        name = child.text.decode("utf8")
                    elif t.type == "type_identifier":
                        name = t.text.decode("utf8")

                    node.add_dependency(
                        Dependency(DependencyType.from_str(t.type), name)
                    )
            if index in function_query_index:
                node.is_function = True
                if func := node.get_func(bv_func_dict):
                    # We only care about functions that also exist in the binary view.
                    node.bv_func = func
                    desired_functions.add(node)
            else:
                all_types.add(node)

            all_nodes.add(node)
        except:
            pass
    return error_nodes


def get_alias_names(alias_match: Set[Node], name: str, language: Language):
    """
    Get all names of the given alias tree-sitter node.

    :param alias_match: A tree-sitter node to search
    :param name: The name of the parent node the alias_match was found in.
    :param language: the tree-sitter Language to use.
    :return: A set of strings with each alias name found.

    """
    alias_names = set()
    name_search = language.query(ALIAS_NAME_QUERY).matches(alias_match)

    for _, capture in name_search:
        if "alias_name" in capture.get:
            # This node has some number of pointer declarators. Get only the name field.
            alias_name = capture["alias_name"][0].text.decode("utf8")
            if alias_name == name:
                # Drop this node to avoid importing errors on duplicate names
                return None
            alias_names.add(alias_name)
    return alias_names


def create_typedef_nodes(
    root: Node,
    language: Language,
    all_type_defs: Set[HeaderQueryNode],
    all_nodes: Set[HeaderQueryNode],
):
    """
    Query the AST and create HeaderQueryNodes for typedefs found.

    :param root: the root tree-sitter node.
    :param language: the tree-sitter Language to use.
    :param all_type_defs: A set of HeaderQueryNodes for all typedefs that contain fields.
    :param all_nodes: A set of all HeaderQueryNodes that have been found so far.
    :param specifier_query_index: An int representing the index of the specifier query within TYPEDEF_QUERIES
    """
    matches = language.query(TYPEDEF_QUERIES).matches(root)

    for _, capture in matches:
        ts_node = capture["node"][0]
        name = capture["name"][0].text.decode("utf8")

        if any(node.name == name for node in all_type_defs):
            # Only want the first match.
            continue

        alias_names = get_alias_names(ts_node, name, language)
        if alias_names is not None:
            # If None, name and alias match so we want to drop.
            node = HeaderQueryNode(
                ts_node, ts_node.type, ts_node.text.decode("utf8"), name
            )
            node.alias = alias_names
            for alias in alias_names:
                # Add as a dependency for stub creation
                node.add_dependency(Dependency(DependencyType.UNSPECIFIED, alias))
            node.add_dependency(
                Dependency(DependencyType.from_str(node.type), name)
            )  # Store the name so we can later search for the definition.

            all_type_defs.add(node)
            if "fields" in capture:  # typedef includes field information
                all_nodes.add(node)  # Also this node for later duplicate checking
        else:
            node = HeaderQueryNode(
                ts_node, ts_node.type, ts_node.text.decode("utf8"), name
            )
            all_type_defs.add(node)


def create_void_function_nodes(
    root: Node,
    language: Language,
    query: str,
    all_nodes: Set[HeaderQueryNode],
    desired_functions: Set[HeaderQueryNode],
    bv_func_dict: Dict[str, bn.Function],
):
    """
    Query the AST and create HeaderQueryNodes for all functions without return types. Update the desired_functions set.

    :param root: the root tree-sitter node.
    :param language: the tree-sitter Language to use.
    :param query: A tree-sitter Query string
    :param all_nodes: A set of all nodes that have been found so far.
    :param desired_functions: A set of all function HeaderQueryNodes that also appear in the BinaryView.
    """
    name = ""
    matches = language.query(query).matches(root)

    for _, capture in matches:
        name = capture["name"][0].text.decode("utf8")
        if any(node.name == name for node in all_nodes):
            continue
        ts_node = capture["node"][0]

        if func := node.get_func(bv_func_dict):
            # We only care about functions that also exist in the binary view.
            node = HeaderQueryNode(
                ts_node, ts_node.type, ts_node.text.decode("utf8"), name
            )
            node.is_function = True
            node.bv_func = func
            desired_functions.add(node)


def select_include_directory() -> Path | None:
    """Select an include directory using a file dialog."""
    if include_dir := bn.get_directory_name_input("Select include directory"):
        return Path(include_dir)

    bn.log_info("No directory selected")
    return None


def present_type_selection_form(names: Iterable[str]) -> List[str]:
    """
    Present the user with a selection form for types to import.

    :param names: A set of names (string) to select.
    :return: A list of names (string) of all types for which the user selected "Yes".
    """
    field_choices = ["No", "Yes"]
    choice_fields = []
    selections = []
    choice_fields.append(
        bn.LabelField(
            "Warning: Importing individual types may cause some functions to fail to import"
        )
    )
    for name in sorted(names):
        choice_fields.append(bn.ChoiceField(str(name), field_choices))

    if bn.get_form_input(choice_fields, "Select Types to Overwrite"):
        for c in choice_fields:
            if isinstance(c, bn.interaction.ChoiceField) and c.result:
                selections.append(c.prompt)
    else:
        bn.log_warn(
            "Selection cancelled, defaulting to no importing of pre-existing types"
        )
    return selections


def create_report(
    func_fail: Dict[str, str],
    func_success: List[HeaderQueryNode],
    type_fail: Dict[str, str],
    undefined: Set[str],
    error_nodes: List[Node],
    error_filenames: List[str],
):
    """
    Generate a markdown report listing all failures and blank stubs.

    :param func_fail: A dictionary of [name, error] for functions that failed to import.
    :param func_success: A list of HeaderQueryNode functions that were successfully imported.
    :param type_fail: A dictionary of [name, error] for types that failed to import.
    :param undefined: A set of names of blank stubs that weren't overwritten.
    :param error_nodes: A list of error Nodes
    :param error_filenames: A list of filenames that `error_nodes` were found in.
    :return: Markdown formatted string for the report.
    """
    body = ""
    if func_success:
        body = "## Successfully imported functions:"
        names = "\n".join(
            [
                f"- [`{node.name}`](binaryninja://?expr={node.name})"
                for node in sorted(func_success, key=lambda n: n.name)
            ]
        )
        body = "\n".join([body, names])

    body = "\n".join(
        [
            body,
            "## Errors and other results:",
            "*The following should be manually reviewed.*",
        ]
    )

    if func_fail:
        sorted_funcs = dict(sorted(func_fail.items()))
        for name, error in sorted_funcs.items():
            sorted_funcs[name] = error.replace("\\n", ". ").replace("\n", ". ")
        body = "\n".join([body, "### Failed to redefine functions: "])
        info = "Most common errors occur due to macros in the function return type. Errors may also occur when importing individual types as some contextual information used in importing is lost if not all dependencies are imported."
        names = "\n".join(
            [
                f"| [`{name}`](binaryninja://?expr={name}) | `{error}` |"
                for name, error in sorted_funcs.items()
            ]
        )
        body = "\n".join([body, info, "\n| Function Name | Reason |\n| --- | --- |"])
        body = "\n".join([body, names, "\n"])

    if type_fail:
        sorted_types = dict(sorted(type_fail.items()))
        body = "\n".join([body, "### Failed to define types:  "])
        info = "This most commonly occurs when there are references to constants defined with `#define`. For example, specifying array sizes: `char array[ARRAY_SIZE]`"

        for name, error in sorted_types.items():
            sorted_types[name] = error.replace("\\n", ". ").replace("\n", ". ")
        names = "\n".join(
            [f"| `{name}` | `{error}` |" for name, error in sorted_types.items()]
        )
        body = "\n".join([body, info, "\n| Type Name | Reason |\n| --- | --- |"])
        body = "\n".join([body, names, "\n"])

    if undefined:
        body = "\n".join([body, "### Blank stubs created:  "])
        info = "Common reasons for blank stubs:  \n Aliases may be represented by blank structs.  \n Types that are referenced but not defined in the selected headers will be represented by a blank struct. \nThis most commonly occurs when system headers are included  "
        names = "\n- ".join([f"`{name}`" for name in sorted(undefined)])
        body = "\n".join([body, info])
        body = "\n- ".join([body, names, "\n"])

    error_count = len(error_nodes)
    if error_count == 0:
        body = "\n".join([body, "Tree sitter found no errors while parsing."])
    else:
        body = "\n".join([body, "### Errors in tree-sitter parsing: "])
        body = "\n".join(
            [
                body,
                f"Tree sitter found {error_count} errors while parsing the following files. Results may be inaccurate.",
                "\n",
            ]
        )
        filenames = "\n".join([f"- {name}" for name in sorted(error_filenames)])
        body = "\n".join([body, filenames])

    if 0 < error_count <= 15:
        # Output error nodes if there are not many.
        body = "\n".join([body, "\n| Code/Text | Error |\n| --- | --- |"])
        for node in error_nodes:
            text = node.text.decode("utf8")
            text = text.replace("\\n", ". ").replace("\n", ". ").replace("|", " ")
            # strip pipes which will break table formatting
            body = "\n".join([body, f"| `{text}` | `{node}` |"])
        body = "\n".join([body, " ", "\n"])

    return body.replace("<", "&lt;").replace(">", "&gt;")


def run(bv: bn.BinaryView):
    """
    Start the HeaderQueryPlugin background task.
    """
    HeaderQueryPlugin(bv).start()


bn.PluginCommand.register(
    "HeaderQuery",
    "Import function types from unprocessed or partial header files",
    run,
)
