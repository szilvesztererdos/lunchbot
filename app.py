import os
import logging
import shlex
import json
import pymongo
from urllib.parse import urlparse
from slack import WebClient
from flask import abort, Flask, jsonify, request
import requests
import bson
import re

# globals
app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("lunchbot")
mongodb_uri = os.environ.get("MONGODB_URI")
try:
    conn = pymongo.MongoClient(mongodb_uri)
    logger.info("MongoDB connection successful.")
except pymongo.errors.ConnectionFailure as e:
    logger.info(f"Could not connect to MongoDB: {e}")

db = conn[urlparse(mongodb_uri).path[1:]]
slack_client = WebClient(os.environ.get('SLACK_ACCESS_TOKEN'))


def slack_api(method, **kwargs):
    api_call = slack_client.api_call(method, **kwargs)
    if api_call.get('ok'):
        return api_call
    else:
        raise ValueError('Connection error!', api_call.get('error'), api_call.get('args'))



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


@app.route("/command/lunchbot-list-restaurants", methods=["POST"])
def handle_list_restaurants():
    try:
        restaurants_markdown = generate_restaurants_markdown()
        response = {
            "response_type": "ephermal",
            "blocks": restaurants_markdown
        }

        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


@app.route("/command/lunchbot-suggest", methods=["POST"])
def handle_suggest():
    try:
        if request.values["text"] == "":
            response = {
                "text": "Please, specify at least one user."
            }
            return jsonify(response)

        # searching for pattern like <@U1234|user>, where we need @U1234
        user_ids_match = re.finditer("@(\S*)\|", request.values["text"])

        users = slack_api("users.list")["members"]
        user_ids = [user["id"] for user in users]

        for user_id_match in user_ids_match:
            user_id = user_id_match.group(1)
            if user_id not in user_ids:
                response = {
                    "text": f"{user_id} is not valid. Please, make sure all users are real!"
                }
                return jsonify(response)

        # TODO: this should be asked from each mentioned user!
        response = get_response_for_asking_time_limit()
        return jsonify(response)
    except Exception as e:
        logger.error(f"ERROR: {e}")
        abort(200)


def get_response_for_asking_time_limit():
    # TODO: put starting user name in welcome text
    return {
        "response_type": "ephermal",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text": "Hi! I'm lunchbot and I'm helping you to choose where to go for lunch.\n" +
                        "First, please give me how much time you have for lunch."
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "20 min"
                        },
                        "value": "20",
                        "action_id": "answer-time-limit-20"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "30 min"
                        },
                        "value": "30",
                        "action_id": "answer-time-limit-30"
                    }
                ]
            }
        ]
    }


def get_blocks_for_asking_price_limit():
    return [
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": "Okay. Now, choose how much money you have for this lunch!"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "700 HUF"
                    },
                    "value": "700",
                    "action_id": "answer-price-limit-700"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "900 HUF"
                    },
                    "value": "900",
                    "action_id": "answer-price-limit-900"
                }
            ]
        }
    ]


def get_blocks_for_asking_tag_exclude(payload):
    # getting tags dynamically from restaurants
    tags_aggregated = list(db.restaurants.aggregate([
        {
            "$group": {
                "_id": 0,
                "tags": {
                    "$push": "$tags"
                }
            }
        },
        {"$addFields": {
            "tags": {
                "$reduce": {
                    "input": "$tags",
                    "initialValue": [],
                    "in": {"$setUnion": ["$$value", "$$this"]}
                }
            }
        }}
    ]))[0]["tags"]

    # generate buttons based on aggregated tags
    elements = [
        {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "I'm finished"
            },
            "value": "finish",
            "style": "primary",
            "action_id": "finish-tag-exclude"
        }
    ]
    for tag in tags_aggregated:
        # checking whether user already filtered out that tag
        query = db["filters"].find({"user": payload["user"]["id"], "tag_exclude": tag})
        element = {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": tag
                },
                "value": tag,
                "action_id": f"answer-tag-exclude-{tag}"
            }
        if query.count() > 0:
            element["style"] = "danger"
        elements.append(
            element
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": "Okay. Now, choose which restaurant tags do you want to exclude!"
            }
        },
        {
            "type": "actions",
            "elements": elements
        }
    ]


@app.route("/actions", methods=["POST"])
def handle_actions():
    payload = json.loads(request.values["payload"])

    if payload["actions"][0]["action_id"].startswith("confirm-add-restaurant"):
        # TODO: handle multiple user submission
        restaurant_to_add = db["temp"].find_one_and_delete({})
        if payload["actions"][0]["action_id"] == "confirm-add-restaurant-true":
            db["restaurants"].insert_one(
                restaurant_to_add
            )
            logger.debug(f"Restaurant added to db: {restaurant_to_add}")
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Successfully added restaurant `{restaurant_to_add['name']}`."
            }
        else:
            response = {
                "response_type": "ephermal",
                "replace_original": "true",
                "text": f"Cancelled to add new restaurant."
            }
    elif payload["actions"][0]["action_id"] == "remove-restaurant":
        restaurant_id_to_remove = payload["actions"][0]["value"]
        restaurant_to_remove = db["restaurants"].find_one({"_id": bson.ObjectId(restaurant_id_to_remove)})
        db["restaurants"].delete_one({"_id": bson.ObjectId(restaurant_id_to_remove)})
        logger.debug(f"Restaurant removed from db: {restaurant_to_remove}")
        blocks_layout = generate_restaurants_markdown()
        blocks_layout.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Successfully removed restaurant `{restaurant_to_remove['name']}`."
            }
        })

        response = {
            "response_type": "ephermal",
            "replace_original": "true",
            "blocks": blocks_layout
        }
    # TODO: this should be asked from each mentioned user
    elif payload["actions"][0]["action_id"].startswith("answer-time-limit"):
        # store time limit value to user in db
        # TODO: these should be replaced
        db["filters"].insert_one(
            {
                "user": payload["user"]["id"],
                "time_limit": int(payload["actions"][0]["value"])
            }
        )

        # ask for price limit
        blocks_layout = get_blocks_for_asking_price_limit()
        blocks_layout.insert(0, {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Successfully chosen time limit as `{payload['actions'][0]['value']} minutes`."
            }
        })
        response = {
            "response_type": "ephermal",
            "replace_original": "true",
            "blocks": blocks_layout
        }
    # TODO: this should be asked from each mentioned user
    elif payload["actions"][0]["action_id"].startswith("answer-price-limit"):
        # store time limit value to user in db
        # TODO: these should be replaced
        db["filters"].insert_one(
            {
                "user": payload["user"]["id"],
                "price_limit": int(payload["actions"][0]["value"])
            }
        )

        # ask for tag exclude
        blocks_layout = get_blocks_for_asking_tag_exclude(payload)
        blocks_layout.insert(0, {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Successfully chosen price limit as `{payload['actions'][0]['value']} HUF`."
            }
        })
        response = {
            "response_type": "ephermal",
            "replace_original": "true",
            "blocks": blocks_layout
        }
    # TODO: this should be asked from each mentioned user
    elif payload["actions"][0]["action_id"].startswith("answer-tag-exclude"):
        # store time limit value to user in db
        # TODO: if the tag is already there, we should delete it (to mimic button switch)
        db["filters"].insert_one(
            {
                "user": payload["user"]["id"],
                "tag_exclude": payload["actions"][0]["value"]
            }
        )

        # show updated tag exclude question
        blocks_layout = get_blocks_for_asking_tag_exclude(payload)
        response = {
            "response_type": "ephermal",
            "replace_original": "true",
            "blocks": blocks_layout
        }

    requests.post(payload["response_url"], json=response)
    return 'OK'


def get_response_for_add_restaurant_confirm(parameters):
    try:
        confirmation_answer = {
            "name": parameters[0],
            "address": parameters[1],
            "initial duration": int(parameters[2]),
            "initial rating": int(parameters[3]),
            "initial price": int(parameters[4]),
            "tags": parameters[5:]
        }
    except ValueError:
        response = {
            "response_type": "ephermal",
            "text": "Duration, rating and price should be numbers, see `/lunchbot-add-restaurant help` for usage."
        }

        return response

    confirmation_answer_pretty = "\n".join(
        [f"{k}: {', '.join(v)}" if isinstance(v, list) else f"{k}: {v}" for k, v in confirmation_answer.items()]
    )

    db['temp'].insert_one(confirmation_answer)

    # TODO: solve multi-user submissions as well (sending some id to temp?)
    response = {
        "response_type": "ephermal",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "plain_text",
                    "text":
                        f"Do you want to add a restaurant with the following parameters?\n{confirmation_answer_pretty}"
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Add restaurant"
                        },
                        "style": "primary",
                        "action_id": "confirm-add-restaurant-true"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Cancel"
                        },
                        "style": "danger",
                        "action_id": "confirm-add-restaurant-false"
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


def generate_restaurants_markdown():
    restaurant_entries = list(db['restaurants'].find())
    restaurants_markdown = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"There are {len(restaurant_entries)} restaurant(s) in Lunchbot's database:"
            }
        },
        {
            "type": "divider"
        }
    ]
    for i, restaurant_entry in enumerate(restaurant_entries):
        restaurant = restaurant_entry["value"]
        restaurant_name_markdown = f"*{i+1}. {restaurant['name']}*\n"
        restaurant_others_markdown = '\n'.join(f"{key}: {value}" for key, value in list(restaurant.items())[2:])
        restaurants_markdown.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": restaurant_name_markdown + restaurant_others_markdown
            }
        })
        restaurants_markdown.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Remove restaurant"
                    },
                    "action_id": "remove-restaurant",
                    "value": str(restaurant_entry["_id"]),
                    "style": "danger",
                    "confirm": {
                        "title": {
                            "type": "plain_text",
                            "text": "Are you sure?"
                        },
                        "confirm": {
                            "type": "plain_text",
                            "text": "Yes"
                        },
                        "deny": {
                            "type": "plain_text",
                            "text": "No"
                        }
                    }
                }
            ]
        })
    return restaurants_markdown
