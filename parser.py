#!/usr/bin/env python3

import os
import sys
import pcre
import xml.etree.ElementTree as ET

def get_attrib_yes_no(node, name, default):
    retval = default
    if name in node.attrib:
        val = node.attrib[name]
        if val == "yes":
            retval = True
        else:
            retval = False  
    return retval

def get_number_list(node, name):
    retval = []
    if name in node.attrib:
        retval = [int(x) for x in node.attrib[name].split(',')]
    return retval

def get_dest(node):
    val = node.attrib['dest']
    if val[-1] == '+':
        return { 'dest': int(val[0::-2]), "append": True}
    return { 'dest': int(val), "append": False}

def get_input(node):
    retval = node.attrib['input']
    if '$INFO' in retval:
        return retval 
    return int(retval[2::])

def get_val(node, name, default=None):
    return node.attrib.get(name, default)


class Expression:
    def __init__(self, node):
        self.repeat = get_attrib_yes_no(node, 'repeat', False)
        self.noclean = get_number_list(node, 'noclean')
        self.trim = get_number_list(node, 'trim')
        self.clear =  get_attrib_yes_no(node, 'clear', False)
        self.regex = node.text

class EmptyExpression:
    def __init__(self, regex):
        self.repeat = False
        self.noclean = []
        self.trim = []
        self.clear =  []
        self.regex = regex
        

class Regex:
    def __init__(self, node):
        self.input = get_input(node)
        self.output = get_val(node, 'output')
        self.dest = get_dest(node)
        self.conditional = get_val(node, 'conditional')
        self.expression = EmptyExpression('(.*)')
        self.children = []
        for child in node:
            if child.tag == 'expression' and child.text is not None:
                self.expression = Expression(child)
            elif child.tag == 'RegExp':
                self.children.append(Regex(child))
        

class Function:
    def __init__(self, node):
        self.dest = get_dest(node)
        self.clearbuffers = get_attrib_yes_no(node, 'clearbuffers', True)
        self.children = [Regex(child) for child in node]
        self.expression = None
        self.input = None

def apply_regex_sub(expression, data, out):
    if data is not None and expression.regex is not None and out is not None:
        retval = ''
        if not expression.repeat:
            matches = [pcre.search(expression.regex, data)]
        else:
            matches = pcre.finditer(expression.regex, data)
        for match in matches:
            if match is None:
                continue
            retval = retval + match.expand(out)
        if retval != '':
            return retval
    return ''

def apply_buffers(data, buffers):
    buf_num = 20
    if data is None:
        return None
    while(buf_num > 0):
        if buffers[buf_num] is not None:
            data = str.replace(data, '$$'+str(buf_num), str(buffers[buf_num]))
        buf_num -= 1
    return data

def output_real(node, buffers):
    for child in node.children:
        buffers = output_real(child, buffers)
    if node.input is not None:
        data = buffers[node.input]
        if node.expression is not None:
            data = apply_regex_sub(node.expression, data, node.output)
        data = apply_buffers(data, buffers)
        if node.expression.clear:
            buffers[node.dest['dest']] = ''
        if node.dest['append']:
            if buffers[node.dest['dest']] is None:
                buffers[node.dest['dest']] = ''
            if data is not None and data is not '':
                buffers[node.dest['dest']] += data
        elif data is not '':
             buffers[node.dest['dest']] = data    
    return buffers

def output(node, buffers):
    if isinstance(node, Function):
        if node.clearbuffers:
            data = buffers[1]
            buffers = [None] * 21
            buffers[1] = data
    buffers = output_real(node, buffers)
    # print(node.dest)

    # for index, val in enumerate(buffers):
    #     display = val
    #     if display is not None and len(display) > 100:
    #         display = display[:100]
    #     print(str(index)+":" + str(display))



    return buffers[node.dest['dest']]
        

def indent(elem, level=0):
    i = "\n" + level*"  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level+1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def main(argv):
    # stuff
    scraper_file = argv[1]
    input_file = argv[2]
    scraper = ET.parse(scraper_file)
    buffers = [None] * 21
    pcre.enable_re_template_mode()
    funcs = {}
    for child in scraper.getroot():
        funcs[child.tag] = Function(child)
        
    with open(input_file, 'r') as inp:
        htmldata = inp.read()
        buffers[1] = htmldata
        details = funcs['GetDetails']
        out = output(details, buffers)
        out_xml = ET.fromstring(out)
        indent(out_xml)
        ET.dump(out_xml)


if __name__ == "__main__":
     main(sys.argv)