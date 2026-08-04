"""Asyncio-based LDM RPC version 6 client.

Implements a pure-Python downstream LDM6 client that connects to an upstream
LDM server over TCP, performs the FEEDME handshake, and receives data products
via the HEREIS and COMINGSOON/BLKDATA mechanisms.

Usage example::

    import asyncio
    from pyldm import LDMClient, LDMFeedtype, LDMProduct

    async def main():
        def on_product(product: LDMProduct):
            print(f"Got {product.size} bytes for {product.name}")

        client = LDMClient(
            server="ldm.example.edu:388",
            feedtype=LDMFeedtype.IDS | LDMFeedtype.DDPLUS,
            pattern=".*",
        )
        client.on_product(on_product)
        await client.run()

    asyncio.run(main())
"""

# stdlib
import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import IntFlag
from typing import Callable, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RPC / LDM6 constants
# ---------------------------------------------------------------------------

LDM_PROG = 300029
LDM_VERS = 6
LDM_PORT = 388

# Procedure numbers (version SIX block in ldm.x)
PROC_NULLPROC = 0
PROC_HEREIS = 1
PROC_FEEDME = 4
PROC_HIYA = 5
PROC_NOTIFICATION = 8
PROC_NOTIFYME = 9
PROC_COMINGSOON = 12
PROC_BLKDATA = 13
PROC_IS_ALIVE = 14

# RPC constants
RPC_CALL = 0
RPC_REPLY = 1
RPC_VERSION = 2
AUTH_NONE_FLAVOR = 0
MSG_ACCEPTED = 0
SUCCESS = 0

# ldm_errt enum values
LDM_OK = 0
LDM_SHUTTING_DOWN = 1
LDM_BADPATTERN = 2
LDM_DONT_SEND = 3
LDM_RESEND = 4
LDM_RESTART = 5
LDM_REDIRECT = 6
LDM_RECLASS = 7

_LDM_ERRT_NAMES = {
    LDM_OK: "OK",
    LDM_SHUTTING_DOWN: "SHUTTING_DOWN",
    LDM_BADPATTERN: "BADPATTERN",
    LDM_DONT_SEND: "DONT_SEND",
    LDM_RESEND: "RESEND",
    LDM_RESTART: "RESTART",
    LDM_REDIRECT: "REDIRECT",
    LDM_RECLASS: "RECLASS",
}

# Special timestamp values
TS_ZERO = (0, 0)  # epoch start
TS_ENDT = (0x7FFFFFFF, 999999)  # far future ("all future products")


# ---------------------------------------------------------------------------
# LDMFeedtype – bitmask enum matching ldm.x / ldm.h
# ---------------------------------------------------------------------------


class LDMFeedtype(IntFlag):
    """LDM feedtype bitmask constants (from ldm.x / ldm.h)."""

    NONE = 0x00000000
    PPS = 0x00000001  # Public Products Service
    DDS = 0x00000002  # Domestic Data Service
    DDPLUS = 0x00000003  # PPS | DDS
    HDS = 0x00000004  # High Res Data Service
    IDS = 0x00000008  # International Data Service
    WMO = 0x0000000F  # PPS|DDS|HDS|IDS
    SPARE = 0x00000010
    UNIWISC = 0x00000020
    PCWS = 0x00000040
    FSL2 = 0x00000080
    FSL3 = 0x00000100
    FSL4 = 0x00000200
    FSL5 = 0x00000400
    FSL = 0x000007C0
    AFOS = 0x00000800
    CONDUIT = 0x00001000
    FNEXRAD = 0x00002000
    NMC = 0x00003800
    NLDN = 0x00004000
    WSI = 0x00008000
    SATELLITE = 0x00010000
    FAA604 = 0x00020000
    GPS = 0x00040000
    SEISMIC = 0x00080000
    CMC = 0x00100000
    NIMAGE = 0x00200000
    NTEXT = 0x00400000
    NGRID = 0x00800000
    NPOINT = 0x01000000
    NGRAPH = 0x02000000
    NOTHER = 0x04000000
    NPORT = 0x07C00000
    NEXRAD3 = 0x08000000
    NEXRAD2 = 0x10000000
    NXRDSRC = 0x20000000
    EXP = 0x40000000
    ANY = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# LDMProduct dataclass
# ---------------------------------------------------------------------------


@dataclass
class LDMProduct:
    """A product received from the LDM server.

    Attributes:
        name: Product identifier (WMO ID / AFOS PIL / etc.)
        feedtype: Feedtype bitmask value.
        size: Declared byte count of the product.
        payload: Raw bytes of the product data.
        arrival_time: Unix timestamp when the product entered the LDM system.
        origin: Hostname of the LDM node that first ingested the product.
        seqno: Product sequence number assigned by the upstream.
        signature: 16-byte MD5 signature of the product.
    """

    name: str
    feedtype: int
    size: int
    payload: bytes
    arrival_time: float = 0.0
    origin: str = ""
    seqno: int = 0
    signature: bytes = field(default_factory=bytes)


# ---------------------------------------------------------------------------
# XDR helpers
# ---------------------------------------------------------------------------


class _XDRReader:
    """Minimal XDR decoder for LDM6 wire data."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def _read(self, n: int) -> bytes:
        if self._pos + n > len(self._data):
            raise EOFError(
                f"XDR underflow: need {n} bytes at pos {self._pos}, "
                f"have {len(self._data) - self._pos}"
            )
        chunk = self._data[self._pos : self._pos + n]
        self._pos += n
        return chunk

    def uint(self) -> int:
        return struct.unpack(">I", self._read(4))[0]

    def int32(self) -> int:
        return struct.unpack(">i", self._read(4))[0]

    def opaque_fixed(self, n: int) -> bytes:
        """Read n bytes of fixed-length opaque data (padded to 4 bytes)."""
        raw = self._read(n)
        pad = (4 - n % 4) % 4
        if pad:
            self._read(pad)
        return raw

    def string(self) -> str:
        """Read an XDR variable-length string."""
        length = self.uint()
        raw = self.opaque_fixed(length)
        return raw.decode("utf-8", "replace")

    def timestamp(self) -> float:
        """Read a timestampt (two int32: tv_sec, tv_usec) → float."""
        tv_sec = self.int32()
        tv_usec = self.int32()
        return tv_sec + tv_usec / 1_000_000.0

    def signature(self) -> bytes:
        """Read a 16-byte MD5 signature (fixed opaque)."""
        return self.opaque_fixed(16)

    def prod_info(self) -> dict:
        """Decode a prod_info structure."""
        arrival = self.timestamp()
        sig = self.signature()
        origin = self.string()
        feedtype = self.uint()
        seqno = self.uint()
        ident = self.string()
        sz = self.uint()
        return {
            "arrival": arrival,
            "signature": sig,
            "origin": origin,
            "feedtype": feedtype,
            "seqno": seqno,
            "ident": ident,
            "sz": sz,
        }

    def prod_class(self) -> dict:
        """Decode a prod_class_t."""
        from_ts = self.timestamp()
        to_ts = self.timestamp()
        count = self.uint()
        specs = []
        for _ in range(count):
            ft = self.uint()
            pat = self.string()
            specs.append({"feedtype": ft, "pattern": pat})
        return {"from": from_ts, "to": to_ts, "specs": specs}

    def fornme_reply(self) -> dict:
        """Decode a fornme_reply_t (FEEDME reply)."""
        code = self.uint()
        name = _LDM_ERRT_NAMES.get(code, f"UNKNOWN({code})")
        result: dict = {"code": name, "raw_code": code}
        if code == LDM_OK:
            result["id"] = self.uint()
        elif code == LDM_RECLASS:
            result["prod_class"] = self.prod_class()
        return result


def _xdr_string(s: str) -> bytes:
    """Encode a string as XDR variable-length string."""
    enc = s.encode("utf-8")
    length = len(enc)
    pad = (4 - length % 4) % 4
    return struct.pack(">I", length) + enc + b"\x00" * pad


def _xdr_opaque_fixed(data: bytes) -> bytes:
    """Encode fixed-length opaque data (padded to 4-byte boundary)."""
    pad = (4 - len(data) % 4) % 4
    return data + b"\x00" * pad


def _tcp_record(payload: bytes) -> bytes:
    """Wrap payload in an RPC TCP record mark (single fragment)."""
    mark = 0x80000000 | len(payload)
    return struct.pack(">I", mark) + payload


def _auth_none() -> bytes:
    """AUTH_NONE credential/verifier pair (flavor=0, len=0 each)."""
    return struct.pack(">IIII", 0, 0, 0, 0)


def _rpc_call(xid: int, prog: int, vers: int, proc: int, body: bytes) -> bytes:
    """Build a complete RPC CALL message."""
    header = struct.pack(
        ">IIIIIII",
        xid,
        RPC_CALL,
        RPC_VERSION,
        prog,
        vers,
        proc,
        AUTH_NONE_FLAVOR,  # cred flavor
    )
    header += struct.pack(">I", 0)  # cred len
    header += struct.pack(">II", AUTH_NONE_FLAVOR, 0)  # verifier
    return header + body


def _rpc_reply_void(xid: int) -> bytes:
    """Build a void RPC REPLY (accepted, success, no result body)."""
    return struct.pack(
        ">IIIII",
        xid,
        RPC_REPLY,
        MSG_ACCEPTED,
        AUTH_NONE_FLAVOR,
        0,  # verifier len
    ) + struct.pack(">I", SUCCESS)


def _rpc_reply_body(xid: int, body: bytes) -> bytes:
    """Build an RPC REPLY with a result body."""
    return _rpc_reply_void(xid) + body


def _encode_prod_class(feedtype: int, pattern: str) -> bytes:
    """Encode a prod_class_t with a single prod_spec."""
    now = time.time()
    # from: now (seconds only, usec=0)
    from_buf = struct.pack(">ii", int(now), 0)
    # to: TS_ENDT (far future)
    to_buf = struct.pack(">ii", TS_ENDT[0], TS_ENDT[1])
    # psa array: count=1, then one prod_spec
    ft_buf = struct.pack(">I", feedtype)
    pat_buf = _xdr_string(pattern)
    psa_buf = struct.pack(">I", 1) + ft_buf + pat_buf
    return from_buf + to_buf + psa_buf


def _encode_feedpar(
    feedtype: int, pattern: str, primary: bool = True
) -> bytes:
    """Encode a feedpar_t (prod_class_t + max_hereis)."""
    pc = _encode_prod_class(feedtype, pattern)
    max_hereis = 0xFFFFFFFF if primary else 0
    return pc + struct.pack(">I", max_hereis)


# ---------------------------------------------------------------------------
# asyncio Protocol
# ---------------------------------------------------------------------------


class _LDM6Protocol(asyncio.Protocol):
    """asyncio Protocol implementing the LDM6 downstream client.

    After connecting, sends a FEEDME call and then acts as an RPC server
    on the same socket to receive HEREIS / COMINGSOON+BLKDATA products.
    """

    def __init__(
        self,
        feedtype: int,
        pattern: str,
        product_callback: Optional[Callable[[LDMProduct], None]],
        primary_mode: bool = True,
    ):
        self._feedtype = feedtype
        self._pattern = pattern
        self._product_callback = product_callback
        self._primary_mode = primary_mode

        self._transport: Optional[asyncio.Transport] = None
        self._buf = b""
        self._fragments = b""  # accumulates multi-fragment RPC record
        self._xid = 1  # next outgoing XDR transaction ID
        self._pending_feedme_xid: Optional[int] = None

        # State for COMINGSOON/BLKDATA reassembly
        self._pending_info: Optional[dict] = None
        self._pending_data = b""

        # Signalled when FEEDME reply is received successfully
        self.feedme_done: asyncio.Event = asyncio.Event()
        # Signalled on connection loss
        self.connection_lost_event: asyncio.Event = asyncio.Event()
        self.connection_lost_exc: Optional[Exception] = None

    # ------------------------------------------------------------------
    # asyncio.Protocol callbacks
    # ------------------------------------------------------------------

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]
        log.debug("Connected to LDM server, sending FEEDME")
        self._send_feedme()

    def data_received(self, data: bytes) -> None:
        self._buf += data
        self._process_buffer()

    def connection_lost(self, exc: Optional[Exception]) -> None:
        self.connection_lost_exc = exc
        self.connection_lost_event.set()
        if not self.feedme_done.is_set():
            self.feedme_done.set()  # unblock any waiter
        log.debug("Connection lost: %s", exc)

    # ------------------------------------------------------------------
    # TCP record framing
    # ------------------------------------------------------------------

    def _process_buffer(self) -> None:
        """Parse RPC TCP record marks and dispatch complete records."""
        while True:
            if len(self._buf) < 4:
                return
            mark = struct.unpack(">I", self._buf[:4])[0]
            last_frag = bool(mark & 0x80000000)
            frag_size = mark & 0x7FFFFFFF
            total = 4 + frag_size
            if len(self._buf) < total:
                return
            fragment = self._buf[4:total]
            self._buf = self._buf[total:]
            self._fragments += fragment
            if last_frag:
                record = self._fragments
                self._fragments = b""
                self._dispatch_rpc(record)

    # ------------------------------------------------------------------
    # RPC dispatch
    # ------------------------------------------------------------------

    def _dispatch_rpc(self, data: bytes) -> None:
        """Decode and dispatch a complete RPC message."""
        try:
            r = _XDRReader(data)
            xid = r.uint()
            mtype = r.uint()  # 0=CALL, 1=REPLY
        except EOFError as exc:
            log.warning("Short RPC message: %s", exc)
            return

        if mtype == RPC_REPLY:
            self._handle_reply(xid, r)
            return

        if mtype != RPC_CALL:
            log.warning("Unknown RPC message type: %s", mtype)
            return

        # Incoming RPC CALL from upstream (role reversal)
        try:
            _rpc_vers = r.uint()  # should be 2
            _prog = r.uint()
            _vers = r.uint()
            proc = r.uint()
            # skip credentials
            for _ in range(2):
                _fl = r.uint()
                n = r.uint()
                if n:
                    r.opaque_fixed(n)
        except EOFError as exc:
            log.warning("Short RPC CALL header: %s", exc)
            return

        try:
            self._dispatch_call(xid, proc, r)
        except Exception as exc:  # noqa: BLE001
            log.exception("Error handling proc %s: %s", proc, exc)

    def _handle_reply(self, xid: int, r: _XDRReader) -> None:
        """Handle an incoming RPC reply (answer to our FEEDME call)."""
        try:
            reply_stat = r.uint()  # 0=MSG_ACCEPTED
            # verifier
            _vfl = r.uint()
            vlen = r.uint()
            if vlen:
                r.opaque_fixed(vlen)
            accept_stat = r.uint()
        except EOFError as exc:
            log.warning("Short RPC reply: %s", exc)
            return

        if reply_stat != MSG_ACCEPTED or accept_stat != SUCCESS:
            log.error(
                "RPC reply not accepted: reply_stat=%s accept_stat=%s",
                reply_stat,
                accept_stat,
            )
            return

        if xid == self._pending_feedme_xid:
            self._handle_feedme_reply(r)

    def _dispatch_call(self, xid: int, proc: int, r: _XDRReader) -> None:
        """Dispatch an incoming RPC CALL from the upstream server."""
        if proc == PROC_NULLPROC:
            self._send_void_reply(xid)
        elif proc == PROC_HEREIS:
            self._handle_hereis(xid, r)
        elif proc == PROC_COMINGSOON:
            self._handle_comingsoon(xid, r)
        elif proc == PROC_BLKDATA:
            self._handle_blkdata(xid, r)
        elif proc == PROC_NOTIFICATION:
            self._handle_notification(xid, r)
        elif proc == PROC_HIYA:
            self._handle_hiya(xid, r)
        else:
            log.debug("Unhandled proc %s from upstream", proc)

    # ------------------------------------------------------------------
    # Outgoing messages
    # ------------------------------------------------------------------

    def _next_xid(self) -> int:
        xid = self._xid
        self._xid = (self._xid + 1) & 0xFFFFFFFF
        return xid

    def _send(self, payload: bytes) -> None:
        if self._transport is not None:
            self._transport.write(_tcp_record(payload))

    def _send_feedme(self) -> None:
        xid = self._next_xid()
        self._pending_feedme_xid = xid
        body = _encode_feedpar(
            self._feedtype, self._pattern, self._primary_mode
        )
        msg = _rpc_call(xid, LDM_PROG, LDM_VERS, PROC_FEEDME, body)
        self._send(msg)
        log.debug(
            "Sent FEEDME xid=%s feedtype=%s pattern=%s",
            xid,
            self._feedtype,
            self._pattern,
        )

    def _send_void_reply(self, xid: int) -> None:
        self._send(_rpc_reply_void(xid))

    def _send_comingsoon_reply(self, xid: int, code: int) -> None:
        body = struct.pack(">I", code)
        self._send(_rpc_reply_body(xid, body))

    def _send_hiya_reply(self, xid: int) -> None:
        # hiya_reply_t{OK, max_hereis=UINT_MAX}
        body = struct.pack(">II", LDM_OK, 0xFFFFFFFF)
        self._send(_rpc_reply_body(xid, body))

    # ------------------------------------------------------------------
    # Incoming call handlers
    # ------------------------------------------------------------------

    def _handle_feedme_reply(self, r: _XDRReader) -> None:
        try:
            reply = r.fornme_reply()
        except EOFError as exc:
            log.error("Could not parse FEEDME reply: %s", exc)
            return
        code = reply["code"]
        if code == "OK":
            log.info(
                "FEEDME accepted by upstream LDM (pid=%s)", reply.get("id")
            )
            self.feedme_done.set()
        elif code == "RECLASS":
            log.info("FEEDME RECLASS: %s", reply.get("prod_class"))
            # Accept the reclassification and continue
            self.feedme_done.set()
        else:
            log.error("FEEDME rejected with code: %s", code)
            self.feedme_done.set()

    def _handle_hiya(self, xid: int, r: _XDRReader) -> None:
        """HIYA — upstream is offering push mode; reply with OK."""
        try:
            pc = r.prod_class()
            log.debug("HIYA: %s", pc)
        except EOFError:
            pass
        self._send_hiya_reply(xid)

    def _handle_hereis(self, xid: int, r: _XDRReader) -> None:
        """HEREIS — complete product in one message (no reply needed)."""
        try:
            info = r.prod_info()
            sz = info["sz"]
            data = r.opaque_fixed(sz) if sz > 0 else b""
        except EOFError as exc:
            log.warning("Truncated HEREIS: %s", exc)
            return
        self._deliver_product(info, data)

    def _handle_comingsoon(self, xid: int, r: _XDRReader) -> None:
        """COMINGSOON — product metadata; BLKDATA chunk(s) will follow."""
        try:
            info = r.prod_info()
            _pktsz = r.uint()
        except EOFError as exc:
            log.warning("Truncated COMINGSOON: %s", exc)
            self._send_comingsoon_reply(xid, LDM_DONT_SEND)
            return
        self._pending_info = info
        self._pending_data = b""
        self._send_comingsoon_reply(xid, LDM_OK)

    def _handle_blkdata(self, xid: int, r: _XDRReader) -> None:
        """BLKDATA — a chunk of the current product (no reply needed)."""
        try:
            _sig = r.signature()
            _pktnum = r.uint()
            dlen = r.uint()
            chunk = r.opaque_fixed(dlen) if dlen > 0 else b""
        except EOFError as exc:
            log.warning("Truncated BLKDATA: %s", exc)
            return

        if self._pending_info is None:
            log.warning("BLKDATA with no preceding COMINGSOON, ignoring")
            return

        self._pending_data += chunk
        if len(self._pending_data) >= self._pending_info["sz"]:
            self._deliver_product(self._pending_info, self._pending_data)
            self._pending_info = None
            self._pending_data = b""

    def _handle_notification(self, xid: int, r: _XDRReader) -> None:
        """NOTIFICATION — metadata only, no data payload (no reply needed)."""
        try:
            info = r.prod_info()
            log.debug(
                "NOTIFICATION: feedtype=%s ident=%s",
                info["feedtype"],
                info["ident"],
            )
        except EOFError as exc:
            log.warning("Truncated NOTIFICATION: %s", exc)

    def _deliver_product(self, info: dict, data: bytes) -> None:
        """Package info+data into LDMProduct and invoke the callback."""
        if info["sz"] == 0:
            # nil product used as keepalive/flush — skip
            return
        product = LDMProduct(
            name=info["ident"],
            feedtype=info["feedtype"],
            size=info["sz"],
            payload=data,
            arrival_time=info["arrival"],
            origin=info["origin"],
            seqno=info["seqno"],
            signature=info["signature"],
        )
        if self._product_callback is not None:
            try:
                self._product_callback(product)
            except Exception:  # noqa: BLE001
                log.exception("Exception in product callback")


# ---------------------------------------------------------------------------
# Public LDMClient API
# ---------------------------------------------------------------------------


class LDMClient:
    """Asyncio-based LDM RPC version 6 downstream client.

    Connects to an upstream LDM server, negotiates feed parameters via FEEDME,
    and delivers received products to a registered callback.

    Example::

        import asyncio
        from pyldm import LDMClient, LDMFeedtype, LDMProduct

        async def main():
            def handler(product: LDMProduct):
                print(f"{product.name}: {product.size} bytes")

            client = LDMClient(
                server="ldm.example.edu:388",
                feedtype=LDMFeedtype.IDS | LDMFeedtype.DDPLUS,
                pattern=".*",
            )
            client.on_product(handler)
            await client.run()

        asyncio.run(main())

    Args:
        server: Hostname and optional port of the LDM server in the form
            ``"hostname"`` or ``"hostname:port"``.  Default port is 388.
        feedtype: Feedtype bitmask specifying which data feeds to subscribe to.
            Use :class:`LDMFeedtype` constants (OR-able).
        pattern: POSIX extended regular expression applied to product
            identifiers (WMO headers / AFOS PILs).
            Defaults to ``".*"`` (all products).
        primary_mode: When ``True`` (default) the server sends complete
            products via ``HEREIS``.  When ``False`` it sends metadata via
            ``COMINGSOON`` followed by data chunks via ``BLKDATA``.
        reconnect_delay: Seconds to wait before reconnecting after a connection
            failure.  Set to ``None`` to disable automatic reconnection.
    """

    def __init__(
        self,
        server: str,
        feedtype: int = LDMFeedtype.ANY,
        pattern: str = ".*",
        primary_mode: bool = True,
        reconnect_delay: Optional[float] = 5.0,
    ):
        host, _, port_str = server.partition(":")
        self._host = host
        self._port = int(port_str) if port_str else LDM_PORT
        self._feedtype = int(feedtype)
        self._pattern = pattern
        self._primary_mode = primary_mode
        self._reconnect_delay = reconnect_delay
        self._product_callback: Optional[Callable[[LDMProduct], None]] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._protocol: Optional[_LDM6Protocol] = None

    def on_product(self, callback: Callable[[LDMProduct], None]) -> None:
        """Register a callback to be called for each received product.

        Args:
            callback: Callable that accepts a single :class:`LDMProduct`
                argument.  It will be invoked from within the asyncio event
                loop; long-running work should be dispatched to a thread pool.
        """
        self._product_callback = callback

    async def stop(self) -> None:
        """Signal :meth:`run` to stop after the current connection closes."""
        if self._stop_event is not None:
            self._stop_event.set()

    async def run(self) -> None:
        """Connect to the LDM server and receive products until stopped.

        This coroutine blocks until :meth:`stop` is called or
        ``reconnect_delay`` is ``None`` and the connection is lost.
        """
        self._stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        while not self._stop_event.is_set():
            protocol = _LDM6Protocol(
                feedtype=self._feedtype,
                pattern=self._pattern,
                product_callback=self._product_callback,
                primary_mode=self._primary_mode,
            )
            self._protocol = protocol
            transport = None
            try:
                transport, _ = await loop.create_connection(
                    lambda p=protocol: p,
                    host=self._host,
                    port=self._port,
                )
                log.info("Connected to %s:%s", self._host, self._port)
                # Wait until FEEDME handshake completes
                await protocol.feedme_done.wait()
                # Keep running until connection is lost or stop() called
                await asyncio.wait(
                    [
                        asyncio.ensure_future(
                            protocol.connection_lost_event.wait()
                        ),
                        asyncio.ensure_future(self._stop_event.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except OSError as exc:
                log.error(
                    "Could not connect to %s:%s: %s",
                    self._host,
                    self._port,
                    exc,
                )
            finally:
                if transport is not None and not transport.is_closing():
                    transport.close()
                self._protocol = None

            if self._stop_event.is_set():
                break
            if self._reconnect_delay is None:
                break
            log.info(
                "Reconnecting in %.1f seconds...", self._reconnect_delay
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._reconnect_delay
                )
                break  # stop() was called during delay
            except asyncio.TimeoutError:
                pass  # normal — delay elapsed, loop again
