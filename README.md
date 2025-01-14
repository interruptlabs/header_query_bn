# HeaderQuery

HeaderQuery is a Binary Ninja plugin designed to annotate functions and import types from unprocessed or incomplete C header files which you otherwise are unable to import through the builtin `Import Header Files...` 

Using [Tree-Sitter](https://tree-sitter.github.io/tree-sitter/) the plugin will search the header files for any functions that appear in the Binary View. It will then query the parameters of those functions and import all dependencies, including nested dependencies.
If this information isn't in the given directory of headers the plugin will create a blank type in the BNDB to allow function/type annotation. 

## Installation

1. Clone the this repository into the [Binary Ninja plugins](https://docs.binary.ninja/guide/plugins.html) directory.
2. Install the Python dependencies: 
```
pip install -r requirements.txt
```

## Usage

1. Open a binary file in Binary Ninja. 
2. From the Plugins drop-down menu select `Partial Header Importer`
3. Select a directory that contains all C header files you want to import from.
**NOTE**: It is not necessary to flatten the directory; the plugin will recursively search nested directories. 
4. When prompted select whether you would like to overwrite types that are already defined in your BNDB.
You may also select individual types to import. 
5. Upon completion, a new tab will be opened with a list of types and/or functions that you may like to manually review, along with some guidance on why you may wish to review them. 

![](assets/HeaderQuery_screen-grab.gif)

## Caveats

This plugin is designed specifically to work on unprocessed C header files.
It does not consider pre-processor directives and will ignore types that rely on macros.
If you have access to the entire library and are able to import through the existing Binary Ninja `Import Header File` functionality, you will get more accurate results.  

It is the users reponsibility to ensure that the headers imported contain valid C code. C code with syntax errors will yield some results, but C++ code will not yield useful results.