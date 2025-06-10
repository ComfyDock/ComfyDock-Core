# utils.py

import random
import string
import ipaddress
from typing import Dict, List, Tuple, Union, Optional
from typing import Tuple
from urllib.parse import quote_plus
import csv
import io
import re
from collections.abc import Mapping, Sequence

def generate_id(length=8):
    """
    Generate a random ID of a given length.
    """
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


PortValue = Union[int, Tuple[str, int], List[int]]
ParsedPorts = Dict[str, PortValue]


def _pick_mapping(
    ports: ParsedPorts, container_port: int = 8188, proto: str = "tcp"
) -> Tuple[str, int]:
    """
    Pick the (host_ip, host_port) pair we’ll advertise as the ComfyUI endpoint.

    Priority:
    1. Exact match for `f"{container_port}/{proto}"`  (e.g. 8188/tcp)
    2. Otherwise the *first* mapping in insertion order.
    """
    key = f"{container_port}/{proto}"
    if key in ports:
        value = ports[key]
    else:
        # Python 3.7+ keeps insertion order for dicts
        key, value = next(iter(ports.items()))

    # ── normalise the value into (ip, port) ───────────────────────────────
    if isinstance(value, int):
        host_ip, host_port = "localhost", value
    elif isinstance(value, tuple):
        host_ip, host_port = value
        # Treat "0.0.0.0" or blank IP as localhost for clickable link
        if host_ip in ("", "0.0.0.0"):
            host_ip = "localhost"
    elif isinstance(value, list):
        host_ip, host_port = "localhost", value[0]
    else:
        raise ValueError(f"Unexpected port mapping value type: {type(value)}")

    return host_ip, host_port


def comfyui_url(
    ports: ParsedPorts,
    container_port: int = 8188,
    proto: str = "tcp",
    scheme: str = "http",
    path: str = "/",
) -> str:
    """
    Build `http://<host_ip>:<host_port>/<path>` from a parse_ports() result.

    Example
    -------
    >>> mapping = parse_ports('8080:8188;127.0.0.1:8090:8189')
    >>> comfyui_url(mapping)
    'http://localhost:8080/'

    Parameters
    ----------
    ports : ParsedPorts
        The dict produced by `parse_ports`.
    container_port : int, default 8188
        Container-side port where ComfyUI listens.
    proto : str, default 'tcp'
        Protocol to look for.
    scheme : str, default 'http'
        URL scheme.  Change to 'https' if your proxy terminates TLS.
    path : str, default '/'
        Additional path to append (e.g. '/queue').
    """
    host_ip, host_port = _pick_mapping(ports, container_port, proto)
    host_ip = quote_plus(host_ip)  # ensure IPv6 etc. stay valid in URL
    path = path.lstrip("/")  # avoid double slashes
    return f"{scheme}://{host_ip}:{host_port}/{path}"


def _validate_port(n: int) -> None:
    if not (1 <= n <= 65_535):
        raise ValueError(f"Invalid port number: {n}")


def _parse_port_expression(expr: str) -> List[int]:
    """
    Convert “8080”, “8080-8082” or “8080,8081,8082” -> [8080, 8081, 8082]
    """
    expr = expr.strip()
    if not expr:
        raise ValueError("Empty port expression")

    # Disallow mixing list + range in a single token
    if "-" in expr and "," in expr:
        raise ValueError("Cannot mix ranges and comma lists in the same expression")

    if "-" in expr:  #  ➜ range (unchanged)…
        start_s, end_s = expr.split("-", 1)
        start, end = int(start_s), int(end_s)
        _validate_port(start)
        _validate_port(end)
        if start > end:
            raise ValueError(f"Port range start > end: {expr}")
        return list(range(start, end + 1))

    if "," in expr:  #  ➜ comma-separated list
        tokens = [t.strip() for t in expr.split(",")]

        # NEW ⟶  detect blank tokens like '', '   ', or trailing comma
        if any(t == "" for t in tokens):
            raise ValueError("Empty element in comma-separated port list")

        ports = [int(t) for t in tokens]
        for p in ports:
            _validate_port(p)
        return ports

    # single port
    port = int(expr)
    _validate_port(port)
    return [port]


def parse_ports(ports_str: str) -> ParsedPorts:
    """
    Parse a port specification string into a dictionary compatible with the Docker SDK.

    The input string should be a semicolon-separated list of port mappings using
    Docker's native format with extensions for advanced use cases.

    Each mapping follows Docker's standard format with enhancements:

    Basic Mappings:
    - "host_port:container_port" (e.g., "8080:8188"): Maps container port to specific host port
      Example: "8080:8188" -> {"8188/tcp": 8080}
    - "container_port" (e.g., "8188"): Exposes container port to same port on host
      Example: "8188" -> {"8188/tcp": 8188}

    Protocol Specification:
    - "host_port:container_port/protocol" (e.g., "8080:8188/udp"): Specify protocol (tcp/udp)
      Example: "8080:8188/udp" -> {"8188/udp": 8080}
      Example: "8188/udp" -> {"8188/udp": 8188}
    - Defaults to TCP if protocol not specified

    Host IP Binding:
    - "host_ip:host_port:container_port" (e.g., "127.0.0.1:8080:8188"): Bind to specific interface
      Example: "127.0.0.1:8080:8188" -> {"8188/tcp": ("127.0.0.1", 8080)}
    - "host_ip::container_port" (e.g., "127.0.0.1::8188"): Bind to same port on specific interface
      Example: "127.0.0.1::8188" -> {"8188/tcp": ("127.0.0.1", 8188)}

    Port Ranges:
    - "host_start-host_end:container_start-container_end" (e.g., "8080-8090:8080-8090"): Map port ranges
      Example: "8080-8082:8180-8182" -> {"8180/tcp": 8080, "8181/tcp": 8081, "8182/tcp": 8082}

    Extensions (Non-Docker Standard):
    - "host_port1,host_port2:container_port" (e.g., "8080,8081:8188"): Map container port to multiple host ports
      Example: "8080,8081:8188" -> {"8188/tcp": [8080, 8081]}

    Args:
        ports_str: The string defining port mappings in Docker-compatible format.

    Returns:
        A dictionary where keys are container ports in "port/protocol" format
        (e.g., "8188/tcp") and values are:
        - int: host port number
        - tuple: (host_ip, host_port) for IP-bound ports
        - list: [host_port1, host_port2, ...] for multiple host ports (extension)

    Raises:
        ValueError: If the ports_str is malformed, contains invalid port numbers,
                    or uses an unsupported format.
    """
    if ports_str is None:
        raise ValueError("ports_str cannot be None")

    ports_str = ports_str.strip()
    if ports_str == "":
        return {}

    result: ParsedPorts = {}
    mappings = [m.strip() for m in ports_str.split(";") if m.strip()]

    for mapping in mappings:
        # --- protocol ------------------------------------------------------
        if "/" in mapping:
            mapping, proto = mapping.rsplit("/", 1)
            proto = proto.lower()
            if proto not in ("tcp", "udp"):
                raise ValueError(f"Unsupported protocol: {proto}")
        else:
            proto = "tcp"

        # -------------------------------------------------------------------
        parts = mapping.split(":")

        # (1) host-ip : host-port : container-port
        if len(parts) == 3:
            host_ip, host_port_expr, cont_expr = (p.strip() for p in parts)

            # validate IP
            try:
                ipaddress.ip_address(host_ip)
            except ValueError:
                raise ValueError(f"Invalid host IP: {host_ip}")

            # “host_ip::container_port”  -> host_port == container_port
            if host_port_expr == "":
                host_port_expr = cont_expr

            host_ports = _parse_port_expression(host_port_expr)
            cont_ports = _parse_port_expression(cont_expr)

            if len(host_ports) != 1 or len(cont_ports) != 1:
                raise ValueError(
                    "IP-bound mappings must use single ports, not lists or ranges"
                )

            host_port = host_ports[0]
            cont_port = cont_ports[0]
            key = f"{cont_port}/{proto}"

            if key in result:
                raise ValueError(f"Duplicate container port mapping: {key}")
            result[key] = (host_ip, host_port)

        # (2) host-thing : container-thing
        elif len(parts) == 2:
            host_expr, cont_expr = (p.strip() for p in parts)
            host_ports = _parse_port_expression(host_expr)
            cont_ports = _parse_port_expression(cont_expr)

            # case 2a – same number of host & container ports (range-to-range)
            if len(cont_ports) > 1:
                if len(host_ports) != len(cont_ports):
                    raise ValueError(
                        "Host and container port ranges must be the same length"
                    )
                for hp, cp in zip(host_ports, cont_ports):
                    key = f"{cp}/{proto}"
                    if key in result:
                        raise ValueError(f"Duplicate mapping for {key}")
                    result[key] = hp
            # case 2b – multiple host ports mapping to ONE container port
            elif len(host_ports) > 1:
                cp = cont_ports[0]
                key = f"{cp}/{proto}"
                if key in result:
                    raise ValueError(f"Duplicate mapping for {key}")
                result[key] = host_ports
            # case 2c – simple 1-to-1 mapping
            else:
                hp = host_ports[0]
                cp = cont_ports[0]
                key = f"{cp}/{proto}"
                if key in result:
                    raise ValueError(f"Duplicate mapping for {key}")
                result[key] = hp

        # (3) container-port only
        elif len(parts) == 1:
            cont_ports = _parse_port_expression(parts[0].strip())
            if len(cont_ports) != 1:
                raise ValueError("Standalone container port must not be a range/list")
            cp = cont_ports[0]
            key = f"{cp}/{proto}"
            if key in result:
                raise ValueError(f"Duplicate mapping for {key}")
            result[key] = cp

        else:
            raise ValueError(f"Unrecognised port mapping: {mapping}")

    return result


# Very loose POSIX-style check; adjust if you need Unicode, dots, etc.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EnvLike = Union[str, Mapping[str, str], Sequence[str], None]

def parse_environment_variables(env_vars: EnvLike) -> Optional[List[str]]:
    """
    Normalise many possible "env-var specs" into the canonical
    ``["KEY=value", ...]`` form that Docker / subprocess expect.

    ────────────────────────────────────────────────────────────────
    ACCEPTED INPUT FORMS
    ────────────────────────────────────────────────────────────────
      • None                        -> returns None
      • dict        {"A":"1"}       -> ["A=1"]
      • list/tuple  ["A=1","B=2"]   -> same list after validation
      • str  CSV-ish
            'A=1,B="foo,bar",C=3'   -> ["A=1", "B=foo,bar", "C=3"]
            'A=1,B=foo\\,bar'       -> ["A=1", "B=foo,bar"]

    VALUE WITH COMMAS
    ─────────────────
    * Wrap the value in single or double quotes:
        MAIL_LIST="a@example.com,b@example.com"
    * OR escape the comma with a backslash:
        MAIL_LIST=a@example.com\\,b@example.com
    """
    if env_vars in ("", None):
        return None

    # ── 1. expand the different shapes into a simple iterable ──────────────────
    if isinstance(env_vars, Mapping):
        tokens = [f"{k}={v}" for k, v in env_vars.items()]
    elif isinstance(env_vars, (list, tuple)):
        tokens = list(env_vars)
    elif isinstance(env_vars, str):
        # Use the csv module so users can quote values containing commas.
        reader = csv.reader(
            io.StringIO(env_vars),
            skipinitialspace=True,
            escapechar="\\",          # allow backslash-escaped commas
        )
        try:
            tokens = next(reader)
        except csv.Error as exc:
            raise ValueError(f"Malformed environment variable string: {exc}") from exc
    else:
        raise TypeError(
            "env_vars must be None, str, list/tuple[str], or Mapping[str,str]"
        )

    # ── 2. validate each token ─────────────────────────────────────────────────
    normalised: List[str] = []
    for raw in tokens:
        if not raw or raw.isspace():
            continue

        if "=" not in raw:
            raise ValueError(
                f"Invalid environment variable '{raw}'. "
                "Expected format KEY=value (value may be quoted)."
            )

        key, value = raw.split("=", 1)
        key, value = key.strip(), value.strip()

        if not _ENV_KEY_RE.fullmatch(key):
            raise ValueError(f"Illegal variable name '{key}'")

        # Strip surrounding single/double quotes (csv already un-quoted, but
        # that also handles single-quoted or mixed manual input).
        if (
            len(value) >= 2
            and ((value[0] == value[-1]) and value[0] in ("'", '"'))
        ):
            value = value[1:-1]

        normalised.append(f"{key}={value}")

    return normalised or None


if __name__ == "__main__":
    result = parse_ports("8080:8188")
    print(result)
    print(comfyui_url(result))
    # {'8188/tcp': 8080}

    result = parse_ports("127.0.0.1:8080:8188/tcp")
    print(result)
    print(comfyui_url(result))
    # {'8188/tcp': ('127.0.0.1', 8080)}

    result = parse_ports("8080-8082:8180-8182")
    print(result)
    print(comfyui_url(result))
    # {'8180/tcp': 8080, '8181/tcp': 8081, '8182/tcp': 8082}

    result = parse_ports("8080,8081:8188/udp")
    print(result)
    print(comfyui_url(result))
    # {'8188/udp': [8080, 8081]}

    result = parse_ports("8188")
    print(result)
    print(comfyui_url(result))
    # {'8188/tcp': 8188}

    result = parse_ports("127.0.0.1::8188")
    print(result)
    print(comfyui_url(result))
    # {'8188/tcp': ('127.0.0.1', 8188)}

    result = parse_ports("8080;8081;8085")
    print(result)
    print(comfyui_url(result))
    # {'8080/tcp': 8080, '8081/tcp': 8081, '8085/tcp': 8085}

    result = parse_ports("8080:8188;127.0.0.1:8090:8189")
    print(result)
    print(comfyui_url(result))
    # {'8188/tcp': 8080, '8189/tcp': ('127.0.0.1', 8090)}

    # {'8188/tcp': ('192.168.1.50', 9000)}
    result = parse_ports("192.168.1.50:9000:8188/tcp")
    print(result)
    print(comfyui_url(result))

