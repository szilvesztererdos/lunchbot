import os
import logging
from slackeventsapi import SlackEventAdapter
from flask import abort, Flask, jsonify, request

# globals
app = Flask(__name__)
slack_events_adapter = SlackEventAdapter(os.environ['SLACK_SIGNING_SECRET'], "/slack/events", app)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('lunchbot')


@app.route('/', methods=['POST'])
def lunchbot():
    try:
        return jsonify(
            response_type='in_channel',
            text='hello there',
        )
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)
