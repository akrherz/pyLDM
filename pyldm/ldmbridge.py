"""
Created on Jan 5, 2013

@author: akrherz
"""
# stdlib
import hashlib
import datetime

# twisted imports
from twisted.internet import stdio
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.python import log


class LDMProductReceiver(basic.LineReceiver):
    """Our Protocol"""
    product_start = '\001'
    product_end = '\r\r\n\003'

    def __init__(self, dedup=False, isbinary=False):
        """Constructor

        Params:
          dedup (boolean): should we attempt to filter out duplicates
          isbinary (boolean): should we not attempt unicode decoding?
        """
        self.productBuffer = u""
        self.setRawMode()
        self.cbFunc = self.process_data
        self.cache = {}
        self.isbinary = isbinary
        if dedup:
            self.cbFunc = self.filter_product
            reactor.callLater(90, self.clean_cache)  # @UndefinedVariable

    def clean_cache(self):
        """Cull old cache every 90 seconds"""
        threshold = datetime.datetime.utcnow() - datetime.timedelta(hours=4)
        for digest in self.cache.keys():
            if self.cache[digest] < threshold:
                del self.cache[digest]
        reactor.callLater(90, self.clean_cache)  # @UndefinedVariable

    def filter_product(self, original):
        """ Implement Deduplication
         - Attempt to account for all of the wild variations that can happen
         with LDM plexing of NOAAPort, WeatherWire, TOC Socket Feed and
         whatever else may be happening

        1. If the character \x1e happens, we ignore it.  SPC issue here
        2. If we find \x17, this is some weather wire thing, we ignore whatever
           comes after it.
        3. We ignore any extraneous trailing space or line returns
        4. We convert tab characters to blank spaces, as one of NWSTG's systems
           does this already and is a source of duplicates
        5. Ignore first 11 bytes in MD5 computation

        If Okay, we end up calling self.process_data() with clean data

        """
        clean = original.replace('\x1e', '').replace('\t', '')
        if clean.find("\x17") > 0:
            # log.msg("control-17 found, truncating...")
            clean = clean[:clean.find("\x17")]
        # log.msg("buffer[:20] is : "+ repr(buf[:20]) )
        # log.msg("buffer[-20:] is : "+ repr(buf[-20:]) )
        lines = clean.split("\015\015\012")
        # Trim trailing empty lines
        while lines and lines[-1].strip() == "":
            lines.pop()
        if not lines:
            log.msg("ERROR: filter_product culled entire product (no data?)")
            return
        lines[1] = lines[1][:3]
        clean = "\015\015\012".join(lines)
        # Our data is unicode and needs to be encoded prior to hashing
        digest = hashlib.md5(clean[11:].encode('utf-8')).hexdigest()
        # log.msg("Cache size is : "+ str(len(self.cache.keys())) )
        # log.msg("digest is     : "+ str(digest) )
        # log.msg("Product Size  : "+ str(len(product)) )
        # log.msg("len(lines)    : "+ str(len(lines)) )
        if digest in self.cache:
            log.msg("DUP! %s" % (",".join(lines[1:5]),))
        else:
            self.cache[digest] = datetime.datetime.utcnow()
            # log.msg("process_data() called")
            self.process_data(clean + "\015\015\012")

    def rawDataReceived(self, data):
        """callback from twisted when raw data is received

        Args:
          data (str): string with assumed utf-8 encoding
        """
        # First thing is first, make sure this is unicode and not some fake
        # str with non-ascii characters floating around
        if not self.isbinary:
            data = data.decode('utf-8')
        # See if we have anything left over from previous iteration
        if self.productBuffer != "":
            data = self.productBuffer + data

        tokens = data.split(self.product_end)
        # If length tokens is 1, then we did not find the splitter
        if len(tokens) == 1:
            # log.msg("Token not found, len data %s" % (len(data),))
            self.productBuffer = data
            return

        # Everything up until the last one can always go...
        for token in tokens[:-1]:
            # log.msg("ldmbridge cb product size: %s" % (len(token),))
            self.cbFunc(token)
        # We have some cruft left over!
        if tokens[-1] != "":
            self.productBuffer = tokens[-1]
        else:
            self.productBuffer = ""

    def connectionLost(self, reason):
        raise NotImplementedError

    def process_data(self, data):
        raise NotImplementedError

    def lineReceived(self, line):
        ''' needless override to make pylint happy '''
        pass


class LDMProductFactory(stdio.StandardIO):

    def __init__(self, protocol, **kwargs):
        """ constructor with a protocol instance """
        stdio.StandardIO.__init__(self, protocol, **kwargs)
