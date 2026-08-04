"""Tests for the asyncio LDM RPC v6 client (pyldm.ldmclient).

These tests are entirely self-contained and do not require a real LDM server.
They exercise:
  - LDMFeedtype bitmask operations
  - LDMProduct dataclass
  - XDR encoding/decoding helpers (_XDRReader, _xdr_string, etc.)
  - TCP record framing (_tcp_record)
  - RPC message building (_rpc_call, _rpc_reply_void, _rpc_reply_body)
  - _LDM6Protocol handling: FEEDME reply, HEREIS, COMINGSOON+BLKDATA,
    HIYA, NOTIFICATION, NULLPROC, multi-fragment records
  - LDMClient public API construction
"""

import asyncio
import struct
import time
import unittest

from pyldm.ldmclient import (
    AUTH_NONE_FLAVOR,
    LDM_BADPATTERN,
    LDM_OK,
    LDM_PROG,
    LDM_RECLASS,
    LDM_VERS,
    PROC_BLKDATA,
    PROC_COMINGSOON,
    PROC_FEEDME,
    PROC_HEREIS,
    PROC_HIYA,
    PROC_NOTIFICATION,
    PROC_NULLPROC,
    RPC_CALL,
    RPC_VERSION,
    LDMClient,
    LDMFeedtype,
    LDMProduct,
    _encode_feedpar,
    _LDM6Protocol,
    _rpc_call,
    _rpc_reply_body,
    _rpc_reply_void,
    _tcp_record,
    _xdr_opaque_fixed,
    _xdr_string,
    _XDRReader,
)


def _make_call(xid: int, proc: int, body: bytes) -> bytes:
    """Build a complete TCP-framed RPC CALL from upstream → downstream."""
    header = struct.pack(
        ">IIIIIII",
        xid,
        RPC_CALL,
        RPC_VERSION,
        LDM_PROG,
        LDM_VERS,
        proc,
        AUTH_NONE_FLAVOR,
    )
    header += struct.pack(">I", 0)  # cred len
    header += struct.pack(">II", AUTH_NONE_FLAVOR, 0)  # verifier
    payload = header + body
    return _tcp_record(payload)


def _make_feedme_reply_ok(xid: int, upstream_pid: int = 12345) -> bytes:
    """Build a TCP-framed FEEDME reply with code=OK and a PID."""
    body = struct.pack(">II", LDM_OK, upstream_pid)
    return _tcp_record(_rpc_reply_body(xid, body))


def _make_feedme_reply_reclass(xid: int) -> bytes:
    """Build a FEEDME RECLASS reply with a minimal prod_class."""
    now = int(time.time())
    pc = struct.pack(">ii", now, 0)          # from
    pc += struct.pack(">ii", 0x7FFFFFFF, 999999)  # to
    pc += struct.pack(">I", 0)               # psa count = 0
    body = struct.pack(">I", LDM_RECLASS) + pc
    return _tcp_record(_rpc_reply_body(xid, body))


def _make_feedme_reply_badpattern(xid: int) -> bytes:
    body = struct.pack(">I", LDM_BADPATTERN)
    return _tcp_record(_rpc_reply_body(xid, body))


def _encode_prod_info(
    ident: str,
    feedtype: int = int(LDMFeedtype.DDPLUS),
    sz: int = 10,
    seqno: int = 1,
    origin: str = "localhost",
    arrival: float = 0.0,
    signature: bytes = b"\x00" * 16,
) -> bytes:
    tv_sec = int(arrival)
    tv_usec = int((arrival - tv_sec) * 1_000_000)
    buf = struct.pack(">ii", tv_sec, tv_usec)   # arrival
    buf += _xdr_opaque_fixed(signature)  # 16 bytes, no padding needed
    buf += _xdr_string(origin)
    buf += struct.pack(">I", feedtype)
    buf += struct.pack(">I", seqno)
    buf += _xdr_string(ident)
    buf += struct.pack(">I", sz)
    return buf




class _FakeTransport:
    """Captures everything written to the "wire"."""

    def __init__(self):
        self.written = b""
        self._closing = False

    def write(self, data: bytes) -> None:
        self.written += data

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True


def _make_protocol(**kwargs) -> tuple[_LDM6Protocol, _FakeTransport]:
    """Create a protocol instance with a fake transport."""
    defaults = {
        "feedtype": int(LDMFeedtype.DDPLUS),
        "pattern": ".*",
        "product_callback": None,
        "primary_mode": True,
    }
    defaults.update(kwargs)
    proto = _LDM6Protocol(**defaults)
    transport = _FakeTransport()
    proto.connection_made(transport)
    return proto, transport




class TestLDMFeedtype(unittest.TestCase):
    def test_basic_values(self):
        self.assertEqual(int(LDMFeedtype.PPS), 0x00000001)
        self.assertEqual(int(LDMFeedtype.DDS), 0x00000002)
        self.assertEqual(int(LDMFeedtype.DDPLUS), 0x00000003)
        self.assertEqual(int(LDMFeedtype.ANY), 0xFFFFFFFF)

    def test_or_combination(self):
        combined = LDMFeedtype.IDS | LDMFeedtype.DDPLUS
        self.assertEqual(int(combined), 0x0000000B)

    def test_contains(self):
        combined = LDMFeedtype.IDS | LDMFeedtype.DDS
        self.assertIn(LDMFeedtype.DDS, combined)
        self.assertNotIn(LDMFeedtype.HDS, combined)




class TestLDMProduct(unittest.TestCase):
    def test_creation(self):
        p = LDMProduct(
            name="WWUS90 KDMX",
            feedtype=int(LDMFeedtype.DDPLUS),
            size=100,
            payload=b"hello world",
        )
        self.assertEqual(p.name, "WWUS90 KDMX")
        self.assertEqual(p.size, 100)
        self.assertEqual(p.payload, b"hello world")
        self.assertEqual(p.arrival_time, 0.0)
        self.assertEqual(p.origin, "")

    def test_full_creation(self):
        sig = b"\xab" * 16
        p = LDMProduct(
            name="SAUS70 KWBC",
            feedtype=int(LDMFeedtype.IDS),
            size=5,
            payload=b"12345",
            arrival_time=1000.5,
            origin="ldm.example.edu",
            seqno=42,
            signature=sig,
        )
        self.assertEqual(p.origin, "ldm.example.edu")
        self.assertEqual(p.seqno, 42)
        self.assertEqual(p.signature, sig)




class TestXDRHelpers(unittest.TestCase):
    def test_xdr_string_empty(self):
        encoded = _xdr_string("")
        self.assertEqual(encoded, struct.pack(">I", 0))

    def test_xdr_string_4byte_aligned(self):
        # "abcd" is 4 bytes → no padding
        encoded = _xdr_string("abcd")
        self.assertEqual(encoded, struct.pack(">I", 4) + b"abcd")

    def test_xdr_string_padding(self):
        # "ab" is 2 bytes → 2 bytes padding
        encoded = _xdr_string("ab")
        self.assertEqual(encoded, struct.pack(">I", 2) + b"ab\x00\x00")

    def test_xdr_opaque_fixed_no_pad(self):
        data = b"\x01\x02\x03\x04"
        self.assertEqual(_xdr_opaque_fixed(data), data)

    def test_xdr_opaque_fixed_with_pad(self):
        data = b"\x01\x02"
        self.assertEqual(_xdr_opaque_fixed(data), b"\x01\x02\x00\x00")

    def test_tcp_record_mark(self):
        payload = b"hello"
        record = _tcp_record(payload)
        mark = struct.unpack(">I", record[:4])[0]
        self.assertTrue(mark & 0x80000000)  # last_frag set
        self.assertEqual(mark & 0x7FFFFFFF, 5)
        self.assertEqual(record[4:], payload)




class TestXDRReader(unittest.TestCase):
    def test_uint(self):
        r = _XDRReader(struct.pack(">I", 42))
        self.assertEqual(r.uint(), 42)

    def test_int32_negative(self):
        r = _XDRReader(struct.pack(">i", -1))
        self.assertEqual(r.int32(), -1)

    def test_string(self):
        data = _xdr_string("hello")
        r = _XDRReader(data)
        self.assertEqual(r.string(), "hello")

    def test_opaque_fixed(self):
        raw = b"\xDE\xAD\xBE\xEF"
        data = _xdr_opaque_fixed(raw)
        r = _XDRReader(data)
        self.assertEqual(r.opaque_fixed(4), raw)

    def test_timestamp(self):
        data = struct.pack(">ii", 1000, 500000)
        r = _XDRReader(data)
        ts = r.timestamp()
        self.assertAlmostEqual(ts, 1000.5)

    def test_signature(self):
        sig = bytes(range(16))
        data = _xdr_opaque_fixed(sig)
        r = _XDRReader(data)
        self.assertEqual(r.signature(), sig)

    def test_prod_info(self):
        ident = "WWUS90 KDMX 090000"
        sz = 100
        raw = _encode_prod_info(ident, sz=sz, seqno=7, origin="testhost")
        r = _XDRReader(raw)
        info = r.prod_info()
        self.assertEqual(info["ident"], ident)
        self.assertEqual(info["sz"], sz)
        self.assertEqual(info["seqno"], 7)
        self.assertEqual(info["origin"], "testhost")

    def test_underflow_raises(self):
        r = _XDRReader(b"\x00\x00")  # only 2 bytes
        with self.assertRaises(EOFError):
            r.uint()

    def test_fornme_reply_ok(self):
        data = struct.pack(">II", LDM_OK, 9999)
        r = _XDRReader(data)
        reply = r.fornme_reply()
        self.assertEqual(reply["code"], "OK")
        self.assertEqual(reply["id"], 9999)

    def test_fornme_reply_badpattern(self):
        data = struct.pack(">I", LDM_BADPATTERN)
        r = _XDRReader(data)
        reply = r.fornme_reply()
        self.assertEqual(reply["code"], "BADPATTERN")




class TestRPCBuilders(unittest.TestCase):
    def test_rpc_call_structure(self):
        body = b"\x01\x02\x03\x04"
        msg = _rpc_call(1, LDM_PROG, LDM_VERS, PROC_FEEDME, body)
        r = _XDRReader(msg)
        self.assertEqual(r.uint(), 1)         # xid
        self.assertEqual(r.uint(), 0)         # CALL
        self.assertEqual(r.uint(), 2)         # RPC version
        self.assertEqual(r.uint(), LDM_PROG)
        self.assertEqual(r.uint(), LDM_VERS)
        self.assertEqual(r.uint(), PROC_FEEDME)

    def test_rpc_reply_void(self):
        msg = _rpc_reply_void(42)
        r = _XDRReader(msg)
        self.assertEqual(r.uint(), 42)        # xid
        self.assertEqual(r.uint(), 1)         # REPLY

    def test_rpc_reply_body(self):
        body = struct.pack(">I", 99)
        msg = _rpc_reply_body(7, body)
        # last 4 bytes should be the body
        self.assertTrue(msg.endswith(body))

    def test_encode_feedpar(self):
        fp = _encode_feedpar(int(LDMFeedtype.DDPLUS), ".*", primary=True)
        # Last 4 bytes are max_hereis=UINT_MAX
        max_hereis = struct.unpack(">I", fp[-4:])[0]
        self.assertEqual(max_hereis, 0xFFFFFFFF)

    def test_encode_feedpar_alternate(self):
        fp = _encode_feedpar(int(LDMFeedtype.DDPLUS), ".*", primary=False)
        max_hereis = struct.unpack(">I", fp[-4:])[0]
        self.assertEqual(max_hereis, 0)




class TestProtocolFeedme(unittest.TestCase):
    def test_feedme_sent_on_connect(self):
        proto, transport = _make_protocol()
        # Something should have been written (the FEEDME call)
        self.assertGreater(len(transport.written), 0)
        # Parse the TCP record
        mark = struct.unpack(">I", transport.written[:4])[0]
        self.assertTrue(mark & 0x80000000)

    def test_feedme_reply_ok_sets_event(self):
        proto, transport = _make_protocol()
        xid = proto._pending_feedme_xid
        reply = _make_feedme_reply_ok(xid, upstream_pid=1111)
        proto.data_received(reply)
        self.assertTrue(proto.feedme_done.is_set())

    def test_feedme_reply_reclass_sets_event(self):
        proto, transport = _make_protocol()
        xid = proto._pending_feedme_xid
        reply = _make_feedme_reply_reclass(xid)
        proto.data_received(reply)
        self.assertTrue(proto.feedme_done.is_set())

    def test_feedme_reply_badpattern_sets_event(self):
        proto, transport = _make_protocol()
        xid = proto._pending_feedme_xid
        reply = _make_feedme_reply_badpattern(xid)
        proto.data_received(reply)
        self.assertTrue(proto.feedme_done.is_set())




class TestProtocolHereis(unittest.TestCase):
    def test_hereis_delivers_product(self):
        received = []

        def cb(p: LDMProduct):
            received.append(p)

        proto, transport = _make_protocol(product_callback=cb)
        # Simulate FEEDME reply to unblock
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        payload = b"SAMPLE DATA HERE"
        info_bytes = _encode_prod_info(
            "WWUS90 KDMX 090000",
            feedtype=int(LDMFeedtype.DDPLUS),
            sz=len(payload),
            seqno=1,
            origin="ldm.example.edu",
        )
        body = info_bytes + _xdr_opaque_fixed(payload)
        msg = _make_call(100, PROC_HEREIS, body)
        proto.data_received(msg)

        self.assertEqual(len(received), 1)
        p = received[0]
        self.assertEqual(p.name, "WWUS90 KDMX 090000")
        self.assertEqual(p.payload, payload)
        self.assertEqual(p.size, len(payload))
        self.assertEqual(p.origin, "ldm.example.edu")

    def test_hereis_nil_product_ignored(self):
        """sz=0 (nil) product should not invoke callback."""
        received = []

        def cb(p: LDMProduct):
            received.append(p)

        proto, transport = _make_protocol(product_callback=cb)
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        info_bytes = _encode_prod_info("NIL", sz=0)
        msg = _make_call(101, PROC_HEREIS, info_bytes)
        proto.data_received(msg)

        self.assertEqual(len(received), 0)




class TestProtocolComingsoonBlkdata(unittest.TestCase):
    def _send_comingsoon(self, proto, xid, ident, data):
        info_bytes = _encode_prod_info(ident, sz=len(data))
        pktsz = struct.pack(">I", 16384)
        body = info_bytes + pktsz
        return _make_call(xid, PROC_COMINGSOON, body)

    def _send_blkdata(self, proto, xid, chunk, sig=b"\x00" * 16, pktnum=0):
        sig_bytes = _xdr_opaque_fixed(sig)
        body = sig_bytes + struct.pack(">II", pktnum, len(chunk))
        body += _xdr_opaque_fixed(chunk)
        return _make_call(xid, PROC_BLKDATA, body)

    def test_comingsoon_blkdata_delivers_product(self):
        received = []

        def cb(p: LDMProduct):
            received.append(p)

        proto, transport = _make_protocol(product_callback=cb)
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        data = b"FULL PRODUCT DATA"
        cs_msg = self._send_comingsoon(proto, 200, "SAUS70 KWBC", data)
        proto.data_received(cs_msg)
        # After COMINGSOON, protocol should have sent an OK reply
        # (we don't check the exact bytes, just that something was written)
        written_after_cs = len(transport.written)
        self.assertGreater(written_after_cs, 0)

        proto.data_received(self._send_blkdata(proto, 201, data))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload, data)
        self.assertEqual(received[0].name, "SAUS70 KWBC")

    def test_blkdata_without_comingsoon_ignored(self):
        received = []

        def cb(p: LDMProduct):
            received.append(p)

        proto, transport = _make_protocol(product_callback=cb)
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        chunk = b"ORPHAN DATA"
        proto.data_received(self._send_blkdata(proto, 300, chunk))
        self.assertEqual(len(received), 0)

    def test_comingsoon_reply_contains_ok(self):
        """COMINGSOON reply should encode LDM_OK (0)."""
        proto, transport = _make_protocol()
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        written_before = len(transport.written)
        data = b"DATA"
        info_bytes = _encode_prod_info("X", sz=len(data))
        pktsz = struct.pack(">I", 16384)
        msg = _make_call(500, PROC_COMINGSOON, info_bytes + pktsz)
        proto.data_received(msg)

        reply_bytes = transport.written[written_before:]
        # Should contain LDM_OK (0) encoded as 4-byte big-endian at the end
        self.assertTrue(reply_bytes.endswith(struct.pack(">I", LDM_OK)))




class TestProtocolHiya(unittest.TestCase):
    def _make_hiya(self, xid):
        now = int(time.time())
        pc = struct.pack(">ii", now, 0)
        pc += struct.pack(">ii", 0x7FFFFFFF, 999999)
        pc += struct.pack(">I", 0)  # empty psa
        return _make_call(xid, PROC_HIYA, pc)

    def test_hiya_sends_reply(self):
        proto, transport = _make_protocol()
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))
        written_before = len(transport.written)
        proto.data_received(self._make_hiya(400))
        # A reply should have been sent
        self.assertGreater(len(transport.written), written_before)




class TestProtocolNotification(unittest.TestCase):
    def test_notification_no_product_callback(self):
        """NOTIFICATION should not raise even with no callback."""
        proto, transport = _make_protocol()
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        info_bytes = _encode_prod_info("NOTIFY", sz=0)
        msg = _make_call(600, PROC_NOTIFICATION, info_bytes)
        proto.data_received(msg)  # Should not raise




class TestProtocolNullproc(unittest.TestCase):
    def test_nullproc_sends_void_reply(self):
        proto, transport = _make_protocol()
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))
        written_before = len(transport.written)
        msg = _make_call(700, PROC_NULLPROC, b"")
        proto.data_received(msg)
        # A reply should have been sent
        self.assertGreater(len(transport.written), written_before)




class TestMultiFragment(unittest.TestCase):
    def test_two_fragment_hereis(self):
        """A product split across two TCP record fragments is reassembled."""
        received = []

        def cb(p: LDMProduct):
            received.append(p)

        proto, transport = _make_protocol(product_callback=cb)
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        payload = b"SPLIT PRODUCT DATA"
        info_bytes = _encode_prod_info(
            "SPLIT", sz=len(payload), feedtype=int(LDMFeedtype.DDPLUS)
        )
        # Build the full RPC CALL body
        from pyldm.ldmclient import (
            AUTH_NONE_FLAVOR,
            LDM_PROG,
            LDM_VERS,
            RPC_CALL,
            RPC_VERSION,
        )

        header = struct.pack(
            ">IIIIIII", 800, RPC_CALL, RPC_VERSION, LDM_PROG, LDM_VERS,
            PROC_HEREIS, AUTH_NONE_FLAVOR,
        )
        header += struct.pack(">I", 0)
        header += struct.pack(">II", AUTH_NONE_FLAVOR, 0)
        full_payload = header + info_bytes + _xdr_opaque_fixed(payload)

        mid = len(full_payload) // 2
        frag1 = full_payload[:mid]
        frag2 = full_payload[mid:]

        # First fragment: last_frag=0
        mark1 = struct.pack(">I", len(frag1))  # last_frag bit NOT set
        # Second fragment: last_frag=1
        mark2 = struct.pack(">I", 0x80000000 | len(frag2))

        proto.data_received(mark1 + frag1)
        self.assertEqual(len(received), 0)  # not yet complete

        proto.data_received(mark2 + frag2)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload, payload)




class TestIncrementalData(unittest.TestCase):
    def test_hereis_byte_by_byte(self):
        """Protocol handles data arriving one byte at a time."""
        received = []

        def cb(p: LDMProduct):
            received.append(p)

        proto, transport = _make_protocol(product_callback=cb)
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        payload = b"BYTE BY BYTE"
        info_bytes = _encode_prod_info("BBB", sz=len(payload))
        body = info_bytes + _xdr_opaque_fixed(payload)
        msg = _make_call(900, PROC_HEREIS, body)

        for byte in msg:
            proto.data_received(bytes([byte]))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload, payload)




class TestConnectionLost(unittest.TestCase):
    def test_connection_lost_sets_event(self):
        proto, transport = _make_protocol()
        self.assertFalse(proto.connection_lost_event.is_set())
        proto.connection_lost(None)
        self.assertTrue(proto.connection_lost_event.is_set())

    def test_connection_lost_unblocks_feedme_event(self):
        proto, transport = _make_protocol()
        self.assertFalse(proto.feedme_done.is_set())
        proto.connection_lost(OSError("refused"))
        self.assertTrue(proto.feedme_done.is_set())
        self.assertIsInstance(proto.connection_lost_exc, OSError)




class TestLDMClient(unittest.TestCase):
    def test_default_port(self):
        c = LDMClient(server="ldm.example.edu")
        self.assertEqual(c._host, "ldm.example.edu")
        self.assertEqual(c._port, 388)

    def test_custom_port(self):
        c = LDMClient(server="ldm.example.edu:1234")
        self.assertEqual(c._port, 1234)

    def test_on_product_registers_callback(self):
        c = LDMClient(server="ldm.example.edu")
        cb = lambda p: None  # noqa: E731
        c.on_product(cb)
        self.assertIs(c._product_callback, cb)

    def test_feedtype_stored_as_int(self):
        c = LDMClient(
            server="ldm.example.edu",
            feedtype=LDMFeedtype.IDS | LDMFeedtype.DDPLUS,
        )
        expected = int(LDMFeedtype.IDS | LDMFeedtype.DDPLUS)
        self.assertEqual(c._feedtype, expected)

    def test_pattern_stored(self):
        c = LDMClient(server="ldm.example.edu", pattern="^SA")
        self.assertEqual(c._pattern, "^SA")

    def test_reconnect_delay_none(self):
        c = LDMClient(server="ldm.example.edu", reconnect_delay=None)
        self.assertIsNone(c._reconnect_delay)

    def test_stop_before_run_does_not_raise(self):
        """Calling stop() before run() should not raise."""
        c = LDMClient(server="ldm.example.edu")

        async def _go():
            # stop() before run() — _stop_event is None, should be a no-op
            await c.stop()

        asyncio.run(_go())




class TestCallbackExceptionIsolation(unittest.TestCase):
    def test_exception_in_callback_does_not_crash_protocol(self):
        def bad_cb(p: LDMProduct):
            raise ValueError("test error from callback")

        proto, transport = _make_protocol(product_callback=bad_cb)
        proto.data_received(_make_feedme_reply_ok(proto._pending_feedme_xid))

        payload = b"DATA"
        info_bytes = _encode_prod_info("ERR", sz=len(payload))
        body = info_bytes + _xdr_opaque_fixed(payload)
        msg = _make_call(999, PROC_HEREIS, body)

        # Should not raise — exception is caught internally
        proto.data_received(msg)


if __name__ == "__main__":
    unittest.main()
