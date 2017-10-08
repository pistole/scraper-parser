#!/usr/bin/env python3

import sys
import os
import os.path
import html
from html.parser import HTMLParser
import json
import urllib.parse
import pcre
import xml.etree.ElementTree as ET


# https://stackoverflow.com/questions/753052/strip-html-from-strings-in-python
# This is not exactly what kodi does, but close enough
class MLStripper(HTMLParser):
    def __init__(self):
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.fed = []
    def handle_data(self, d):
        self.fed.append(d)
    def get_data(self):
        return ''.join(self.fed)
    def error(self, message):
        print(message)
        raise(message)

def strip_tags(html):
    s = MLStripper()
    s.feed(html)
    return s.get_data()



def get_attrib_yes_no(node, name, default):
    retval = default
    if node is not None and name in node.attrib:
        val = node.attrib[name]
        if val == "yes":
            retval = True
        elif val == "no":
            retval = False  
    return retval

def get_number_list(node, name):
    retval = []
    if node is not None and name in node.attrib:
        retval = [int(x) for x in node.attrib[name].split(',')]
    return retval

def get_dest(node):
    if node is not None:
        val = node.attrib['dest']
        if val[-1] == '+':
            return { 'dest': int(val.replace('+', '')), "append": True}
        return { 'dest': int(val), "append": False}
    return {'dest': 1, "append": False}

def get_input_text(text):
    retval = text
    if retval == '':
        return 1
    if '$INFO' in retval:
        retval = retval.partition('[')[-1].partition(']')[0]
        return retval 
    return int(retval[2::])



def get_input(node):
    if node is not None:
        return get_input_text(node.attrib['input'])
    return 1

def get_val(node, name, default=None):
    if node is not None:
        return node.attrib.get(name, default)
    return default


class Expression:
    def __init__(self, node):
        self.repeat = get_attrib_yes_no(node, 'repeat', False)
        self.noclean = get_number_list(node, 'noclean')
        self.trim = get_number_list(node, 'trim')
        self.clear =  get_attrib_yes_no(node, 'clear', False)

        # I think what this does is provide a substring that must be in the output
        #  for it to be considered valid, but I can't find a scraper that uses it
        #  and there's no documentation anywhere about it
        # Probably never implement this
        self.compare = get_val(node, 'compare')

        # does some weird html entity to unicode mapping
        # May need to implement this
        self.fixchars = get_number_list(node, 'fixchars')

        # URL encode the results
        self.encode = get_number_list(node, 'encode')
        self.regex = '(.*)'
        if node is not None and node.text not in ('', None):
            self.regex = node.text
        # Probably need to implement this
        self.case_sensitive = get_attrib_yes_no(node, 'cs', False)

        # yes, no or auto
        # I'll probably never implement this
        self.use_utf8 = get_attrib_yes_no(node, 'utf8', None)

 

class Regex:
    def __init__(self, node):
        self.input = get_input(node)
        self.output = get_val(node, 'output')
        self.dest = get_dest(node)
        self.conditional = get_val(node, 'conditional')
        self.expression = Expression(None)
        self.children = []
        for child in node:
            if child.tag == 'expression':
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


class StrippedMatch(pcre.REMatch):
    def __init__(self, pattern, string, pos, endpos, flags, trim=False, noclean=False, encode=False, fixchars=False):
        super().__init__(pattern, string, pos, endpos, flags)
        self.trim = trim
        self.cleanup = not noclean
        self.encode = encode
        self.fixchars = fixchars

    def clean(self, field):
        if field is None:
            return None
        if self.trim:
            field = field.strip()
        if self.cleanup:
            field = strip_tags(field)
        if self.encode:
            field = urllib.parse.quote_plus(field)
        if self.fixchars:
            # FIXME, kodi actually only replaces a whitelist of about 150 entities
            field = html.unescape(field)
        return field

    def groups(self, default=None):
        retval = pcre.REMatch.groups(self, default)
        return tuple(self.clean(x) for x in retval)

    def group(self, *args):
        retval = pcre.REMatch.group(self, *args)
        if retval is None:
            return retval
        if isinstance(retval, str):
            return self.clean(retval)
        else:
            return tuple(self.clean(x) for x in retval)

def apply_regex_sub(expression, data, out):
    if data is not None and expression.regex is not None and out is not None:
        retval = ''
        flags = pcre.M
        if not expression.case_sensitive:
            flags = flags | pcre.I
        if not expression.repeat:
            matches = [pcre.search(expression.regex, data, flags)]
        else:
            matches = pcre.finditer(expression.regex, data, flags)
        i = 0
        for match in matches:
            i+=1
            if match is None:
                continue
            match = StrippedMatch(match.re, match.string, match.pos, match.endpos, match.flags, i in expression.trim, i in expression.noclean, i in expression.encode)
            retval = retval + match.expand(out)
        if retval != '':
            return retval
    return ''

def apply_buffers(data, buffers, config):
    buf_num = 20
    if data is None:
        return None
    while(buf_num > 0):
        if buffers[buf_num] is not None:
            data = str.replace(data, '$$'+str(buf_num), str(buffers[buf_num]))
        buf_num -= 1
    for key in config.keys():
        data = str.replace(data, '$INFO[{0}]'.format(key), config[key])
    return data

def output_real(node, buffers, config):
    buffers_status(buffers,2)
    if isinstance(node, Regex) and node.conditional is not None:
        config_setting = node.conditional
        check_val = "true"
        if config_setting[0] == '!':
            check_val = "false"
            config_setting = config_setting[1:]
        if config_setting in config:
            if check_val != config[config_setting]:
                return buffers
        else:
            print("warning: conditional setting not found: {0}".format(config_setting))
            return buffers

    for child in node.children:
        buffers = output_real(child, buffers, config)
    if node.input is not None:
        if isinstance(node.input, int):
            data = buffers[node.input]
        else:
            # FIXME handle configuration somehow
            data = config.get(node.input, '')
        if node.expression is not None:
            data = apply_regex_sub(node.expression, data, node.output)
        data = apply_buffers(data, buffers, config)
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

def buffers_status(buffers, only_index=None):
    # debugging output
    for index, val in enumerate(buffers):
        if only_index is not None and index != only_index:
            continue
        display = val
        if display is not None and len(display) > 100:
            display = display[:100]
        # print(str(index)+":" + str(display))

def actual_url(url):
    # this is stupid, but it's how the imdb scraper functions work
    if '|' in url:
        # HACK none of the scrapers I have apply more
        # than one query param this way, so just replace it
        url = url.replace('|', '?')
    return url


def output(function, source, buffers, config, funcs):
    node = funcs[function]
    buffers_status(buffers)
    if isinstance(node, Function):
        if node.clearbuffers:
            if isinstance(source, int):
                data = buffers[source]
            else:
                data = config[source]
            buffers = buffers[0:3] + [None] * 18
            if isinstance(source, int):
                buffers[source] = data
    # print(data)
    buffers = output_real(node, buffers, config)
    # print(node.dest['dest'])
    dest = buffers[node.dest['dest']]
    buffers_status(buffers)
    # print(dest)
    if dest is not None:
        details = ET.fromstring(dest)
        chain_out = []
        chain_in = []
        for child in details:
            if child.tag == 'chain':
                chain_in.append(child)
                child_buffers = [None]*21
                child_buffers[1] = child.text
                child_buffers[2] = child.text
                child_buffers[3] = buffers[3]
                chain = output(child.attrib['function'], 1, child_buffers, config, funcs)
                if (chain is None):
                    print("warning: no result from " + child.attrib['function'])
                chain_out.append(chain)
        for child in chain_in:
            details.remove(child)
        for child in chain_out:
            if child is not None:
                for res in ET.fromstring(child):
                    details.append(res)
        dest = ET.tostring(details)
    return dest
        

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

def import_module(module_path, funcs, config, submodule=False):
        print("loading addon {0}".format(module_path))
        addon_folder = os.path.abspath(os.path.join(module_path, os.pardir))
        addon_xml = os.path.join(module_path, 'addon.xml')
        if not os.path.exists(addon_xml):
            print("warning: addon not found {0}".format(module_path))
            return
        settings_file = os.path.join(module_path, 'resources', 'settings.xml')
        if os.path.exists(settings_file):
            cf = ET.parse(settings_file).findall('./category/setting')

            for setting in cf:
                if setting.attrib.get('type', '') in ['sep', 'lsep']:
                    continue
                # print(setting.attrib)
                config[setting.attrib['id']] = setting.attrib['default']
        addon_tree = ET.parse(addon_xml)
        extensions = addon_tree.findall('extension')
        for ext in extensions:
            if 'library' in ext.attrib:
                scraper_file = os.path.join(module_path, ext.attrib['library'])
        scraper = ET.parse(scraper_file)
        children = scraper.getroot()
        for child in children:
            funcs[child.tag] = Function(child)
        
        for module in addon_tree.findall('./requires/import'):
            module_path = os.path.join(addon_folder, module.attrib['addon'])
            import_module(module_path, funcs, config, True)


def main(argv):
    # stuff

    if len(argv) < 4:
        print('Usage: {0} <addon_path_or_xml_file> <function> <local_html_file> [item_id]'.format(argv[0]))
        return
    addon = argv[1]
    function = argv[2]
    input_file = argv[3]
    html_id = 'placeholder'
    if len(argv) > 4:
        html_id = argv[4]
    config = {}
    funcs = {}
    buffers = [None] * 21
    pcre.enable_re_template_mode()

    if addon.lower().endswith('.xml'):
        scraper_file = addon
        scraper = ET.parse(scraper_file)
        for child in scraper.getroot():
            funcs[child.tag] = Function(child)
    else:
        import_module(addon, funcs, config)        
    
    json.dumps(config)

    with open(input_file, 'r') as inp:
        htmldata = inp.read()
        # pcre doesn't seem to be respecting the multiline flag, so join all the lines
        htmldata = htmldata.replace('\n', '').replace('\r', '')
        buffers[1] = htmldata
        buffers[2] = html_id
        buffers[3] = input_file
        details = funcs[function]
        out = output(function, 1, buffers, config, funcs)
        out_xml = ET.fromstring(out)
        indent(out_xml)
        ET.dump(out_xml)


if __name__ == "__main__":
     main(sys.argv)
