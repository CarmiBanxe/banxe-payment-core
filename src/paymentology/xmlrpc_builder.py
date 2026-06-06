"""XML-RPC request/response builder for Paymentology Companion API."""
from typing import Any
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring


def build_request(method_name: str, params: list[tuple[str, Any]]) -> str:
    root = Element("methodCall")
    SubElement(root, "methodName").text = method_name
    params_el = SubElement(root, "params")
    for param_type, param_value in params:
        param_el = SubElement(params_el, "param")
        value_el = SubElement(param_el, "value")
        type_el = SubElement(value_el, param_type)
        type_el.text = str(param_value)
    return '<?xml version="1.0"?>' + tostring(root, encoding="unicode")


def parse_response(xml_text: str) -> dict[str, Any]:
    root = fromstring(xml_text)
    fault = root.find(".//fault")
    if fault is not None:
        return {"error": True, "faultCode": -1, "faultString": "XML-RPC Fault"}
    result = {}
    for member in root.findall(".//member"):
        name = member.find("name").text
        value_el = member.find("value")
        int_el = value_el.find("int")
        if int_el is None:
            int_el = value_el.find("i4")
        if int_el is not None:
            result[name] = int(int_el.text)
            continue
        string_el = value_el.find("string")
        if string_el is not None:
            result[name] = string_el.text or ""
            continue
        dt_el = value_el.find("dateTime.iso8601")
        if dt_el is not None:
            result[name] = dt_el.text
            continue
        result[name] = value_el.text or ""
    return result
