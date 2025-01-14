"""
Contains all tree-sitter queries necessary to find functions, types, typedefs and dependencies.
"""

# Example:
#
# `typedef struct name {//members} alias;`
# `typedef union name {// members} alias;`
# Will match against arbitrary number of aliases, with or without arbitrary number of pointers.
TYPEDEF_SPECIFIER_QUERY = """
(type_definition 
	type: (_ 
		name: (type_identifier)@name 
        body: (_)@fields )@specifier
    declarator: (_)@alias) @node 
    """

# Example:
# `typedef struct name alias;`
TYPEDEF_QUERY = """
(type_definition
	type: (_
    	name: (type_identifier)@name)@specifier
    declarator: (_)@alias)@node     
    """

# Example:
# `typedef long name;`
TYPEDEF_SIZED = """
(type_definition
	type: (sized_type_specifier)
    declarator: (type_identifier)@name)@node
"""

# Example:
# `typedef int name;`
TYPEDEF_PRIMITIVE = """
(type_definition
	type: (primitive_type)
    declarator: (type_identifier)@name)@node
"""

# Matches against the field containining the name (without pointers) when used against a TYPEDEF matched node.
ALIAS_NAME_QUERY = """
declarator: (type_identifier)@alias_name
"""


# Example: ` struct name { // Member definition };`
STRUCT_SPECIFIER_QUERY = """
(struct_specifier
		name: (type_identifier)@name
        body: (field_declaration_list))@node
"""

# Example: `return_type foo(const a *, char b);`
FUNCTION_DECLARATION = """
(declaration 
	type: (_) @return_type) @node  
"""

FUNCTION_DEFINITION = """
(function_definition
	type: (_) @return_type)@node
"""

# Example: `return_type name( // Parameters ); `
FUNCTION_NAME = """
declarator: (function_declarator
    declarator: (identifier)@name)@node
"""

# Example: `enum name { // Members };`
ENUM_SPECIFIER_QUERY = """
(enum_specifier 
	name: (type_identifier)@name
    body: (enumerator_list)) @node
"""

# Matches against a typed field in a struct node.
# Example:
# `struct foo { enum name; };`
# `struct foo { struct name; };`
SPECIFIER_FIELD_DECLARATION = """
(field_declaration
	type: (_
    name: (type_identifier)@name)) 
"""

# Example: `struct foo { name foo; };`
# A field declaration is one instance of `name foo` within a field list. Does not match
FIELD_DECLARATION = """
(field_declaration
	type: (type_identifier)@name)
"""

# Example: `void foo(name a, name b)`
# A Parameter declaration is one instance of `name a` within a parameter list
# `(name a, name b)`
PARAMETER_DECLARATION = """
(parameter_declaration
 	 type: (type_identifier)@name)
"""

# Matches against a typed parameter in a function node. Example:
# `void foo(enum name)`
# `void foo(struct name)`
SPECIFIER_PARAMETER_DECLARATION = """
(parameter_declaration
 	 type: (_  
     name: (type_identifier)@name)@type)
"""

# Example: ``void foo(struct name)`


# Catches any error nodes in the tree
ERROR_NODE = """
(ERROR) @error
"""

QUERY = " ".join(
    f"({s})"
    for s in (
        FUNCTION_DECLARATION,
        FUNCTION_DEFINITION,
        ERROR_NODE,
        STRUCT_SPECIFIER_QUERY,
        ENUM_SPECIFIER_QUERY,
    )
)


TYPEDEF_QUERIES = " ".join(
    f"({s})"
    for s in (
        TYPEDEF_SPECIFIER_QUERY,
        TYPEDEF_QUERY,
        TYPEDEF_SIZED,
        TYPEDEF_PRIMITIVE,
    )
)

DEPENDENCY_QUERY = " ".join(
    f"({s})"
    for s in (
        FIELD_DECLARATION,
        SPECIFIER_FIELD_DECLARATION,
        PARAMETER_DECLARATION,
        SPECIFIER_PARAMETER_DECLARATION,
    )
)
