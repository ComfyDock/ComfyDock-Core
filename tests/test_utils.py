import pytest
from src.comfydock_core.utils import parse_ports

# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------

def test_empty_ports():
    result = parse_ports("")
    assert result == {}

    result = parse_ports("   ")
    assert result == {}


def test_single_container_port():
    result = parse_ports("8188")
    assert result == {"8188/tcp": 8188}

    result = parse_ports("8188/udp")
    assert result == {"8188/udp": 8188}

    # Surrounding whitespace is tolerated
    result = parse_ports(" 8188 ")
    assert result == {"8188/tcp": 8188}


def test_host_to_container_port_mapping():
    result = parse_ports("8080:8188")
    assert result == {"8188/tcp": 8080}

    result = parse_ports("8080:8188/udp")
    assert result == {"8188/udp": 8080}

    result = parse_ports(" 8080:8188 ")
    assert result == {"8188/tcp": 8080}


def test_multiple_host_ports_to_container():
    result = parse_ports("8080,8081:8188")
    assert result == {"8188/tcp": [8080, 8081]}

    result = parse_ports("8080,8081:8188/udp")
    assert result == {"8188/udp": [8080, 8081]}

    # three host ports
    result = parse_ports("8080,8081,8082:8188")
    assert result == {"8188/tcp": [8080, 8081, 8082]}


def test_ip_binding_specific_port():
    result = parse_ports("127.0.0.1:8080:8188")
    assert result == {"8188/tcp": ("127.0.0.1", 8080)}

    result = parse_ports("127.0.0.1:8080:8188/udp")
    assert result == {"8188/udp": ("127.0.0.1", 8080)}

    # 0.0.0.0 to bind all interfaces
    result = parse_ports("0.0.0.0:8080:8188")
    assert result == {"8188/tcp": ("0.0.0.0", 8080)}


def test_ip_binding_same_port():
    result = parse_ports("127.0.0.1::8188")
    assert result == {"8188/tcp": ("127.0.0.1", 8188)}

    result = parse_ports("127.0.0.1::8188/udp")
    assert result == {"8188/udp": ("127.0.0.1", 8188)}


def test_port_ranges():
    result = parse_ports("8080-8082:8180-8182")
    assert result == {
        "8180/tcp": 8080,
        "8181/tcp": 8081,
        "8182/tcp": 8082,
    }

    result = parse_ports("8080-8081:8180-8181/udp")
    assert result == {
        "8180/udp": 8080,
        "8181/udp": 8081,
    }

    # Equal single-port range is equivalent to simple mapping
    result = parse_ports("8080-8080:8180-8180")
    assert result == {"8180/tcp": 8080}


def test_multiple_port_mappings():
    result = parse_ports("8188;8080:8189;8081,8082:8190")
    assert result == {
        "8188/tcp": 8188,
        "8189/tcp": 8080,
        "8190/tcp": [8081, 8082],
    }

    result = parse_ports("8188/udp;8080:8189/tcp;127.0.0.1:8090:8190")
    assert result == {
        "8188/udp": 8188,
        "8189/tcp": 8080,
        "8190/tcp": ("127.0.0.1", 8090),
    }


def test_edge_cases():
    # Port number boundaries
    result = parse_ports("1:1;65535:65535")
    assert result == {
        "1/tcp": 1,
        "65535/tcp": 65535,
    }

    # Large range (5 ports)
    result = parse_ports("8080-8084:8180-8184")
    expected = {f"{8180 + i}/tcp": 8080 + i for i in range(5)}
    assert result == expected

    # Whitespace around semicolons tolerated
    result = parse_ports("8188 ; 8080:8189")
    assert result == {"8188/tcp": 8188, "8189/tcp": 8080}


def test_docker_standard_compatibility():
    assert parse_ports("80:8080") == {"8080/tcp": 80}
    assert parse_ports("127.0.0.1:80:8080") == {"8080/tcp": ("127.0.0.1", 80)}
    assert parse_ports("80:8080/udp") == {"8080/udp": 80}

    expected = {f"{8080 + i}/tcp": 8080 + i for i in range(11)}
    assert parse_ports("8080-8090:8080-8090") == expected

# ---------------------------------------------------------------------------
# Negative / error cases
# ---------------------------------------------------------------------------


def test_invalid_port_numbers():
    # Ports outside 1-65535 (single / range / list)
    for spec in [
        "0",
        "65536",
        "0:8188",
        "65536:8188",
        "8080,65536:8188",
        "127.0.0.1:0:8188",
        "0-8080:8180-8190",
    ]:
        with pytest.raises(ValueError):
            parse_ports(spec)


def test_invalid_ip_addresses():
    for spec in [
        "999.999.999.999:8080:8188",
        "not.an.ip:8080:8188",
        ":8080:8188",
    ]:
        with pytest.raises(ValueError):
            parse_ports(spec)


def test_invalid_port_ranges():
    # Host/Container ranges of unequal length
    with pytest.raises(ValueError):
        parse_ports("8080-8082:8180-8181")

    # start > end in host or container range
    with pytest.raises(ValueError):
        parse_ports("8082-8080:8180-8182")
    with pytest.raises(ValueError):
        parse_ports("8080-8082:8182-8180")


def test_malformed_port_specifications():
    # non‑numeric tokens
    with pytest.raises(ValueError):
        parse_ports("not_a_port")
    with pytest.raises(ValueError):
        parse_ports("not_a_port:8188")
    with pytest.raises(ValueError):
        parse_ports("8080,not_a_port:8188")

    # missing container port
    with pytest.raises(ValueError):
        parse_ports("8080:")

    # missing host port in IP binding ("ip::port" is valid, but "ip:::" is not)
    with pytest.raises(ValueError):
        parse_ports("127.0.0.1:::")

    # empty element between commas
    with pytest.raises(ValueError):
        parse_ports("8080,,8081:8188")

    # trailing slash without protocol
    with pytest.raises(ValueError):
        parse_ports("8080:8188/")


# ---------------------------------------------------------------------------
# New negative cases based on spec review
# ---------------------------------------------------------------------------


def test_duplicate_container_ports_error():
    with pytest.raises(ValueError):
        parse_ports("8080:8188;8081:8188")


def test_mix_range_and_list_in_same_expr():
    with pytest.raises(ValueError):
        parse_ports("8080,8081-8083:8188")


def test_ip_binding_with_multiple_host_ports_error():
    for spec in [
        "127.0.0.1:8080,8081:8188",
        "127.0.0.1:8080-8081:8188",
    ]:
        with pytest.raises(ValueError):
            parse_ports(spec)


def test_mismatched_list_lengths_error():
    with pytest.raises(ValueError):
        parse_ports("8080,8081:8188,8189,8190")