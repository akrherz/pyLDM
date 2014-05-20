'''
Created on Jan 5, 2013

@author: akrherz
'''
#stdlib
import hashlib
import datetime

#twisted imports
from twisted.internet import stdio
from twisted.protocols import basic
from twisted.internet import reactor
from twisted.python import log

class LDMProductReceiver(basic.LineReceiver):
    product_start = '\001'
    product_end = '\r\r\n\003'

    def __init__(self, dedup=False):
        self.productBuffer = ""
        self.setRawMode()
        self.cbFunc = self.process_data
        self.cache = {}
        if dedup:
            self.cbFunc = self.filter_product
            reactor.callLater(90, self.clean_cache)

    def clean_cache(self):
        ''' Cull old cache every 90 seconds '''
        threshold = datetime.datetime.utcnow() - datetime.timedelta(hours=4)
        for m in self.cache.keys():
            if self.cache[m] < threshold:
                del(self.cache[m])
        reactor.callLater(90, self.clean_cache)

    def filter_product(self, buf):
        ''' Filter the product (deduplication) '''
        buf = buf.replace('\x1e', '').replace('\t', '')
        lines = buf.split("\015\015\012")
        # Trim trailing empty lines    
        while len(lines) > 0 and lines[-1] == "":
            lines.pop()
        if len(lines) == 0:
            log.msg("Whoa, filterProduct hit no lines?")
            return
        product = "\015\015\012".join( lines )
        digest = hashlib.md5( product ).hexdigest()
        #log.msg("Cache size is"+ str(len(self.cache.keys())) )
        #log.msg("digest is"+ str(digest) )
        #log.msg("Product Size"+ str(len(product)) )
        #log.msg("line0:"+ lines[0] +":")
        if self.cache.has_key(digest):
            log.msg("DUP! %s" % (",".join(lines[1:5]),) )
        else:
            self.cache[ digest ] = datetime.datetime.utcnow()
            #log.msg("process_data() called")
            self.process_data( product + "\015\015\012")


    def rawDataReceived(self, data):
        """ callback for when raw data is received on the stdin buffer, this 
        could be a partial product or lots of products """
        # See if we have anything left over from previous iteration
        if self.productBuffer != "":
            data = self.productBuffer + data
        
        tokens = data.split(self.product_end)
        # If length tokens is 1, then we did not find the splitter
        if len(tokens) == 1:
            #log.msg("Token not found, len data %s" % (len(data),))
            self.productBuffer = data
            return

        # Everything up until the last one can always go...        
        for token in tokens[:-1]:
            #log.msg("ldmbridge cb product size: %s" % (len(token),))
            self.cbFunc( token )
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

class LDMProductFactory( stdio.StandardIO ):

    def __init__(self, protocol, **kwargs):
        """ constructor with a protocol instance """
        stdio.StandardIO.__init__(self, protocol, **kwargs)




