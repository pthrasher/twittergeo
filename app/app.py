import sys
import os
import time
import json
import urlparse
import urllib

import brukva

import tornado.web
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.ioloop import IOLoop

import tornadio
from tornadio.server import SocketServer

# 
# Globals
#

SUBSCRIBERS = []
TWEETS = None

REDIS_INFO = urlparse.urlparse(os.environ.get('REDISTOGO_URL', 'redis://localhost'))
REDIS_PORT = int(REDIS_INFO.port) if REDIS_INFO.port else 6379
REDIS_PARAMS = {
    'host': REDIS_INFO.hostname,
    'port': REDIS_PORT,
    'password': REDIS_INFO.password,
    'selected_db': 0,
}
REDIS_PUB = brukva.Client(**REDIS_PARAMS)
REDIS_SUB = brukva.Client(**REDIS_PARAMS)
REDIS_CHANNEL = 'twitter' 

# File not in repo. Requires two variables to be defined:
# TWITTER_USER, TWITTER_PASS
from twitter_credentials import *

TWITTER_API_URL = 'https://stream.twitter.com/1/statuses/filter.json' 
ENTIRE_EARTH = urllib.urlencode({'locations': '-180,-90,180,90'})

STREAMING_CLIENT = AsyncHTTPClient()

#
# utility functions
#

def empty_cb(data):
    pass


def run_next(fn):
    IOLoop.instance().add_callback(fn)


def extract_coords(tweet):
    geo = tweet.get('geo')
    if geo:
        geo_type = geo.get('type')
        if geo_type.lower() != 'point':
            return None

        lat, lon = geo.get('coordinates')
    else:
        place = tweet.get('place')
        if not place:
            return None
        bounding_box = place.get('bounding_box')
        if not bounding_box:
            return None
        coords = bounding_box.get('coordinates')
        if not coords:
            return None
        lat, lon = coords[0][0]

    return (lat, lon,)

# 
# Pub-Sub code
#
def tweet_publisher(chunk):
    if chunk: # sometimes the lines are empty.
        REDIS_PUB.publish(REDIS_CHANNEL, chunk)


def kickoff_tweet_stream():
    REDIS_PUB.connect()
    REQ_PARAMS = {
        'method': 'POST',
        'auth_username': TWITTER_USER,
        'auth_password': TWITTER_PASS,
        'body': ENTIRE_EARTH,
        'streaming_callback': tweet_publisher,
        'request_timeout':86400,
    }
    STREAMING_CLIENT.fetch(TWITTER_API_URL, empty_cb, **REQ_PARAMS)


def tweet_subscriber(message):
    data = message.body
    if not data:
        return

    tweet = json.loads(data)

    coords = extract_coords(tweet)        
    if not coords or len(coords) < 2:
        return

    lat, lon = coords

    for c in SUBSCRIBERS:
        try:
            c.send("%.3f|||%.3f|||%s" % (lat, lon, tweet.get('text', '')))
        except:
            if c in SUBSCRIBERS:
                SUBSCRIBERS.remove(c)

def kickoff_redis_subscription():
    global TWEETS
    REDIS_SUB.connect()
    REDIS_SUB.subscribe(REDIS_CHANNEL)
    TWEETS = REDIS_SUB.listen(tweet_subscriber)

# 
# HTTP Handlers
#


class IndexHandler(tornado.web.RequestHandler):
    """Regular HTTP handler to serve the map page"""
    def get(self):
        self.render("index.html")


class GeoHandler(tornadio.SocketConnection):
    """Socket.IO handler"""

    def on_open(self, *args, **kwargs):
        global SUBSCRIBERS
        SUBSCRIBERS.append(self)

    def on_close(self):
        global SUBSCRIBERS
        if self in SUBSCRIBERS:
            SUBSCRIBERS.remove(self)



#
# URL related
#
GeoRouter = tornadio.get_router(GeoHandler)
urls = [
    (r"/", IndexHandler),
    GeoRouter.route()
]

# 
# App settings
#
settings = {
        'enabled_protocols': ['websocket', 'flashsocket', 'xhr-multipart', 'xhr-polling'],
        'socket_io_port': os.environ.get('PORT', 80),
        'socket_io_address': '0.0.0.0',
        'static_path': os.path.join(os.path.dirname(__file__), "static"),
}


if __name__ == "__main__":
    application = tornado.web.Application(urls, **settings)
    socketio_server = SocketServer(application, auto_start=False) #spin up the server

    kickoff_tweet_stream()
    kickoff_redis_subscription()

    IOLoop.instance().start()


