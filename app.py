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
db = {
    "restaurants": []
}


@app.route("/command/lunchbot-add-restaurant", methods=["POST"])
def handle_add_restaurant():
    try:
        text = request.values["text"]
        parameters = shlex.split(text)

        if text.startswith("help"):
            response = get_response_for_add_restaurant_help()
        elif len(parameters) < 6:
            response = get_response_for_add_restaurant_few_arguments()
        else:
            response = get_response_for_add_restaurant_confirm(parameters)

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/actions", methods=["POST"])
def handle_actions():
    payload = json.loads(request.values["payload"])

    if payload["actions"][0]["name"] == "confirm-add-restaurant":
        restaurant_to_add = db.pop("temp_restaurant", None)
        if payload["actions"][0]["value"] == "true":
            db["restaurants"].append(restaurant_to_add)
            logger.debug(f"Restaurant added to db: {restaurant_to_add}")
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Successfully added restaurant {restaurant_to_add['name']}."
            }
        else:
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Cancelled to add new restaurant."
            }

    return jsonify(response)


def get_response_for_add_restaurant_confirm(parameters):
    try:
        confirmation_answer = {
            "name": parameters[0],
            "address": parameters[1],
            "initial duration": int(parameters[2]),
            "initial rating": int(parameters[3]),
            "initial price": int(parameters[4]),
            "tags": ', '.join(parameters[5:])
        }
    except ValueError:
        response = {
            "response_type": "ephermal",
            "text": "Duration, rating and price should be numbers, see `/lunchbot-add-restaurant help` for usage."
        }

        return response

    confirmation_answer_pretty = json.dumps(
        confirmation_answer, ensure_ascii=False, indent=0
    )[1:-1].replace('"', '')

    db['temp_restaurant'] = confirmation_answer

    response = {
        "response_type": "ephermal",
        "text": "Do you want to add a restaurant with the following parameters?",
        "attachments": [
            {
                "text": confirmation_answer_pretty,
                "callback_id": "add-restaurant",
                "actions": [
                    {
                        "name": "confirm-add-restaurant",
                        "type": "button",
                        "text": "Add restaurant",
                        "value": "true",
                        "style": "primary"
                    },
                    {
                        "name": "confirm-add-restaurant",
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


def get_response_for_add_restaurant_help():
    response = {
        "response_type": "ephermal",
        "text": """
Usage: `/lunchbot-add-restaurant <"name"> <"address"> <initial duration in minutes> <initial rating 1-5> <initial price> <"tags" separated by spaces>`
(e.g. `/lunchbot-add-restaurant "Suppé" "1065 Budapest, Hajós u. 19." 30 4 1100 hash-house small-place`)
"""
    }

    return response


def get_response_for_add_restaurant_few_arguments():
    response = {
        "response_type": "ephermal",
        "text": "Too few arguments, see `/lunchbot-add-restaurant help` for usage."
    }

    return response