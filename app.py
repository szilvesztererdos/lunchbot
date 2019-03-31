import os
import logging
import shlex
import json
from slackeventsapi import SlackEventAdapter
from flask import abort, Flask, jsonify, request

# globals
app = Flask(__name__)
slack_events_adapter = SlackEventAdapter(os.environ["SLACK_SIGNING_SECRET"], "/slack/events", app)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("lunchbot")

# constants
COMMAND_ADD_RESTAURANT = "add restaurant"


@app.route("/", methods=["POST"])
def lunchbot():
    try:
        text = request.values["text"]
        if text.startswith(COMMAND_ADD_RESTAURANT):
            parameters = shlex.split(text.strip(COMMAND_ADD_RESTAURANT))
            confirmation_answer = {
                "Name": parameters[0],
                "Address": parameters[1],
                "Initial duration": parameters[2],
                "Initial rating": parameters[3],
                "Tags": ', '.join(parameters[4:])
            }
            confirmation_answer_pretty = json.dumps(
                confirmation_answer, ensure_ascii=False, indent=0
            )[1:-1].replace('"', '')

            response = {
                "response_type": "ephermal",
                "text": "Do you want to add a restaurant with the following parameters?",
                "attachments": [
                    {
                        "text": confirmation_answer_pretty,
                        "actions": [
                            {
                                "name": "add",
                                "type": "button",
                                "text": "Add restaurant",
                                "value": "true",
                                "style": "primary"
                            },
                            {
                                "name": "cancel",
                                "type": "button",
                                "text": "Cancel",
                                "value": "false",
                                "style": "danger"
                            }
                        ]
                    }
                ]
            }
        else:
            response = {
                "response_type": "ephermal",
                "text": "There's no such command."
            }
        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)
