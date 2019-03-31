import os
import logging
from flask import abort, Flask, jsonify, request


app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('lunchbot')


def is_request_valid(request):
    is_token_valid = request.form['token'] == os.environ['SLACK_VERIFICATION_TOKEN']
    is_team_id_valid = request.form['team_id'] == os.environ['SLACK_TEAM_ID']

    return is_token_valid and is_team_id_valid


@app.route('/', methods=['POST'])
def lunchbot():
    if not is_request_valid(request):
        abort(400)

    return jsonify(
        response_type='in_channel',
        text='hello there',
    )
