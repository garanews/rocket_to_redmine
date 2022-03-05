import os
import logging
import threading
import shlex
import configparser
import urllib3
import pytz
import dateutil.parser as dp
from redminelib import Redmine
from datetime import datetime, timedelta

utc = pytz.UTC
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.basicConfig(level=logging.ERROR)
MODE = "rocketchat_API"

if not os.path.exists("latest.txt"):
    with open("latest.txt", "w") as f:
        f.write(
            str((datetime.now() - timedelta(hours=2, minutes=1)).replace(tzinfo=utc))
        )


config = configparser.ConfigParser()
config.read("config.ini")

if MODE == "rocketchat":
    from rocketchat.api import RocketChatAPI

    api = RocketChatAPI(
        settings={
            "username": config["ROCKETCHAT"]["USERNAME"],
            "password": config["ROCKETCHAT"]["PASSWORD"],
            "domain": config["ROCKETCHAT"]["DOMAIN"],
        }
    )
elif MODE == "rocketchat_API":
    from rocketchat_API.rocketchat import RocketChat

    proxy_dict = {"http": config["PROXY"]["HTTP"], "https": config["PROXY"]["HTTPS"]}

    api = RocketChat(
        config["ROCKETCHAT"]["USERNAME"],
        config["ROCKETCHAT"]["PASSWORD"],
        server_url=config["ROCKETCHAT"]["DOMAIN"],
        proxies=proxy_dict,
        ssl_verify=False,
    )

SECONDS = int(config["GENERAL"]["SECONDS"])
CHANNEL = config["ROCKETCHAT"]["CHANNEL"]
REDMINE_PRJ = config["REDMINE"]["PROJECT"]

redmine = Redmine(
    config["REDMINE"]["DOMAIN"],
    username=config["REDMINE"]["USERNAME"],
    password=config["REDMINE"]["PASSWORD"],
    requests={"verify": False},
)

commands = ["#", "?", "+"]


def quote(value):
    return '"' + value + '"' if value.find(" ") != -1 and value[0] != '"' else value


def unquote(value):
    return value[1:-1] if value[0] == '"' and value[-1] == '"' else value


def create_description(all_data):
    issue_data = {}
    issue_strings = []

    # CREATE DICT WITH ALL FIELD AND LIST WITH STRING
    for line in all_data:
        # ALL = BECOME +=
        if line.find("+=") != -1:
            param, value, mode = line.split("+=")[0], "+=".join(line.split("+=")[1:]), 'add'
        elif line.find("-=") != -1:
            param, value, mode = line.split("-=")[0], "+=".join(line.split("-=")[1:]), 'delete'            
        elif line.find("=") != -1:
            param, value, mode = line.split("=")[0], "+=".join(line.split("=")[1:]), 'add'
        else:
            issue_strings.append(line)
            continue

        # DESCRIPTION HAS ALWAYS "" AND IS CONCATENATE IF UPDATED
        if param == "description":

            # DESCRIPTION IS NOT -=able
            if mode == 'delete': 
                continue

            # IF EXISTS
            if len(issue_data.get(param, [])) > 0:
                # CONCATENATE
                issue_data[param] = [(mode, quote(
                    "%s %s" % (unquote(issue_data[param][0][1]), unquote(value))
                ).replace("\n", " "))]
            else:
                issue_data[param] = [(mode, quote(value).replace("\n", " "))]

        elif value not in issue_data.get(param, []):
            issue_data.setdefault(param, []).append((mode, quote(value)))

    new_description = ""
    for value in issue_strings:
        new_description += "\n%s" % value

    for key, value_list in issue_data.items():
        for item_data in sorted(value_list):
            (mode, value) = item_data
            if mode == 'add':
                new_description += "\n%s+=%s" % (quote(key), quote(value))
            else:
                new_description += "\n%s-=%s" % (quote(key), quote(value))

    return new_description


def check_messages():
    threading.Timer(SECONDS, check_messages).start()

    if MODE == "rocketchat":
        messages = [
            (x.get("msg", None), x.get("ts", None), x.get("id", None), x.get("u",{}).get("username", None))
            for x in api.get_room_history(
                CHANNEL, oldest=(datetime.now() - timedelta(hours=2, seconds=SECONDS))
            ).get("messages", None)
            if x.get("msg", "@")[0] in commands
        ]
    elif MODE == "rocketchat_API":
        messages = [
            (x.get("msg", None), x.get("ts", None), x.get("id", None), x.get("u",{}).get("username", None))
            for x in api.channels_history(
                CHANNEL, oldest=(datetime.now() - timedelta(hours=2, seconds=SECONDS))
            )
            .json()
            .get("messages", [])
            if x.get("msg", "@")[0] in commands
        ]
    logging.error(str(datetime.now()) + " - " + str(len(messages)))

    if len(messages) > 0:
        new_messages = []
        for (x, ts, x_id, username) in messages:

            command = x[0]

            with open("latest.txt", "r") as f:
                LATEST = dp.parse(f.readlines()[0])

            if dp.parse(ts).replace(tzinfo=utc) > LATEST:
                with open("latest.txt", "w") as f:
                    f.write(str(dp.parse(ts).replace(tzinfo=utc)))
                    logging.error("update last date")
            else:
                continue

            # ADD EVENT
            if command in ["#", "+"]:

                if command == "+":
                    x = x[1:]

                # Split everything
                x = shlex.split(x)
                title = x[0]

                # new add title as first description
                description = x if command == "#" else x[1:]

                logging.error("search for " + title)
                issues = [x for x in redmine.issue.all() if x["subject"] == title]

                if len(issues) == 0 and command == "#":
                    issue = redmine.issue.create(
                        project_id=REDMINE_PRJ,
                        subject="%s" % title,
                        description=create_description(description),
                        custom_fields=[{'id': 1, 'value': username}]
                    )
                    new_messages = ["@@@ %d created" % issue.id]
                    logging.error("issue created")

                elif len(issues) == 1 and command == "+":
                    issue = issues[0]
                    try:
                        description = shlex.split(issue.description) + description
                    except:
                        # if no description error :|
                        pass
                    issue.description = create_description(description)
                    issue.custom_fields = [{'id': 1, 'value': username}]
                    issue.save()
                    new_messages = ["@@@ %d updated" % issue.id]
                    logging.error("issue updated")

                elif len(issues) > 0 and command == "#":
                    new_messages = ["@@@ title already exists"]
                    logging.error("@@@ title already exists")

                elif len(issues) == 0 and command == "+":
                    new_messages = ["@@@ issue not found"]
                    logging.error("issue " + title + " not found")

            # QUERY EVENT
            elif x[0] == "?":
                if len(x) == 1:
                    issues = redmine.issue.all()
                    new_messages = [
                        "@@@ %d %s %s" % (issue.id, issue.subject, issue.url)
                        for issue in issues
                    ]
                else:
                    title = x[1:].strip()
                    try:
                        title = int(title)
                        issue = redmine.issue.get(title)
                        new_messages = [
                            "@@@ %d %s %s" % (issue.id, issue.subject, issue.url)
                        ]
                    except:
                        logging.error("command non valid")

        for new_message in new_messages:
            if MODE == "rocketchat":
                api.send_message(new_message, CHANNEL)
            else:
                api.chat_post_message(new_message, CHANNEL)
            logging.error(new_message)


if __name__ == "__main__":
    check_messages()
