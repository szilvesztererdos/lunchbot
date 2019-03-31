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


@app.route("/", methods=["POST"])
def handle_add_restaurant():
    try:
        text = request.values["text"]
        parameters = shlex.split(text)

        if text.startswith("help"):
            response = handle_add_restaurant_help()
        elif len(parameters) < 5:
            response = handle_add_restaurant_few_arguments()
        else:
            response = get_response_for_add_restaurant_confirm(parameters)

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


def get_response_for_add_restaurant_confirm(parameters):
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
    return response


def handle_add_restaurant_help():
    response = {
        "response_type": "ephermal",
        "text": """
Usage: `/lunchbot-add-restaurant <"name"> <"address"> <initial duration in minutes> <initial rating 1-5> <"tags" separated by spaces>`
(e.g. `/lunchbot-add-restaurant "Suppé" "1065 Budapest, Hajós u. 19." 30 4 hash-house small-place`)
"""
    }

    return response


def handle_add_restaurant_few_arguments():
    response = {
        "response_type": "ephermal",
        "text": "Too few arguments, see `/lunchbot-add-restaurant help` for usage."
    }

    return response