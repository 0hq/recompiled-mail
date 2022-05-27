import os
import email
import smtplib, ssl
import imaplib
import json
import stripe
import time, threading
import dateutil.parser
from pymongo import MongoClient
from datetime import datetime, timedelta
from dotenv import load_dotenv, find_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

sched = BlockingScheduler()


load_dotenv(find_dotenv())
EMAIL = os.getenv('EMAIL')
PASSWORD = os.getenv('PASSWORD')
IMAP_SERVER = os.getenv('IMAP_SERVER')
SMTP_SERVER = os.getenv('SMTP_SERVER')
PORT = 465  # For SSL

StartTime = time.time()
context = ssl.create_default_context()
url = os.getenv('MONGO_URL')
client = MongoClient("mongodb+srv://server:Pfi88XLO8TrqSgqY@cluster0.ztv48.mongodb.net/Main?retryWrites=true&w=majority")
main_db = client.Main
wdb = main_db.Writers
adb = main_db.Admin
# connect to the server and go to its inbox
mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL, PASSWORD)
mail.select('inbox') # we choose the inbox but you can select others
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
stripe.api_version = os.getenv('STRIPE_API_VERSION', '2019-12-03')

def action() :
    print('action ! -> time : {:.1f}s'.format(time.time()-StartTime))

class setInterval :
    def __init__(self,interval,action) :
        self.interval=interval
        self.action=action
        self.stopEvent=threading.Event()
        thread=threading.Thread(target=self.__setInterval)
        thread.start()

    def __setInterval(self) :
        nextTime=time.time()+self.interval
        while not self.stopEvent.wait(nextTime-time.time()) :
            nextTime+=self.interval
            self.action()

    def cancel(self) :
        self.stopEvent.set()

def send_email(receiver_email, content):
    with smtplib.SMTP_SSL(SMTP_SERVER, PORT, context=context) as server:
        server.login(EMAIL, PASSWORD)
        server.sendmail(EMAIL, receiver_email, content)

# ---------- setup area -------------

def cron_job():
    search_unseen()
    check_invite_expiry()
    check_payout_users()
 
def check_invite_expiry():
    now = datetime.now()
    ws = wdb.find({ "accepted": False, "expired": False}) # gets all invited writers
    print("Check Invite Expiry. Checking:", len(list(ws.clone())))
    for w in ws:
        if w["start_date"] < (now - timedelta(days=7)):
            print("Expire for", w["email"])
            expire_invite(w["email"], w["genesis_inviter"]) 

def search_unseen():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL, PASSWORD)
    mail.select('inbox') # we choose the inbox but you can select others
    now = datetime.now()
    # search_string = now.strftime("%d-%b-%Y")
    search = f'(UNSEEN)'
    # we'll search using the ALL criteria to retrieve
    # every message inside the inbox
    # it will return with its status and a list of ids
    status, data = mail.search(None, search)
    # the list returned is a list of bytes separated
    # by white spaces on this format: [b'1 2 3', b'4 5 6']
    # so, to separate it first we create an empty list
    mail_ids = []
    # then we go through the list splitting its blocks
    # of bytes and appending to the mail_ids list
    for block in data:
        # the split function called without parameter
        # transforms the text or bytes into a list using
        # as separator the white spaces:
        # b'1 2 3'.split() => [b'1', b'2', b'3']
        mail_ids += block.split()

    # now for every id we'll fetch the email
    # to extract its content
    print("Search Unseen. # of new emails:", len(mail_ids))
    for i in mail_ids:
        # the fetch function fetch the email given its id
        # and format that you want the message to be
        status, data = mail.fetch(i, '(RFC822)')

        # the content data at the '(RFC822)' format comes on
        # a list with a tuple with header, content, and the closing
        # byte b')'
        for response_part in data:
            # so if its a tuple...
            if isinstance(response_part, tuple):
                # we go for the content at its second element
                # skipping the header at the first and the closing
                # at the third
                message = email.message_from_bytes(response_part[1])

                # with the content we can extract the info about
                # who sent the message and its subject
                mail_from = message['from']
                mail_subject = message['subject']

                # then for the text we have a little more work to do
                # because it can be in plain text or multipart
                # if its not plain text we need to separate the message
                # from its annexes to get the text
                if message.is_multipart():
                    mail_content = ''

                    # on multipart we have the text message and
                    # another things like annex, and html version
                    # of the message, in that case we loop through
                    # the email payload
                    for part in message.get_payload():
                        # if the content type is text/plain
                        # we extract it
                        if part.get_content_type() == 'text/plain':
                            mail_content += part.get_payload()
                else:
                    # if the message isn't multipart, just extract it
                    mail_content = message.get_payload()

                # and then let's show its result
                print(f'From: {mail_from}')
                print(f'Subject: {mail_subject}')

                split = mail_content.split('XXX')
                sender = mail_from[mail_from.find("<")+1:mail_from.find(">")]
                for x in split:
                    print(x)
                    if len(x) == 8:
                        print(f'Success on split len 8: {x}')      
                        if check_code(x, sender):
                            split.remove(x)
                            c = ' '.join(split)
                            dispatch_email(sender, mail_subject, c)
                            return
                        # else:
                        #     send_error_email(sender)
                        #     return
                
                send_error_email(sender)

def get_strikes(strikes):
    now = datetime.now()
    x = 0
    for s in strikes:
        if s > (now - timedelta(days=60)):
            x += 1
    return x

# -------------------------- main funcs above ------------
                        
def expire_invite(writer, inviter):
    inviter_content = f'''\
Subject: Expired Invite

I'm sorry to say the invitation to {writer} has expired.
A refund is being sent now to {inviter} - if there's any issue here, be sure to reach out to support@recompiled.fyi.

Want to try again? You can issue another invite at recompiled.fyi.

Have a nice day!
- Will DePue
    '''
    writer_content = f'''\
Subject: Expired Invite

I'm sorry to say your invitation from {inviter} has expired.

Want to try again? Reach out to {inviter} and ask them to create a new invite - or you can sign up and start now on recompiled.fyi

Have a nice day!
- Will DePue
    '''
    send_email(inviter, inviter_content)
    send_email(writer, writer_content) 
    cancel_all_subs(writer)
    wdb.update_one({'email': writer },{'$set': {'expired': True}})

def warning_email(writer, subs):
    inviter_content = f'''\
Subject: Alert of Missed Writing Period

Alert:
{writer} has missed this month's writing period. If they miss next month, this subscription will be permanently expired.
If you want to cancel your subscription now, you can do so at recompiled.fyi.

Have a nice day!
- Will DePue
    '''
    writer_content = f'''\
Subject: Alert of Missed Writing Period

You've missed this month's writing period.
If you miss next month, this subscription will be permanently expired.
If you want to cancel your letter now, you can do so at recompiled.fyi.

Have a nice day!
- Will DePue
    '''
    send_email(writer, writer_content) 
    for s in subs:
        send_email(s["email"], inviter_content)
    
def expire_subscription(writer, subs):
    inviter_content = f'''\
Subject: Your subscription has been removed.

Alert:
{writer} has missed their writing period two months in a row. This subscription has been permanently expired. You can always restart this subscription by creating a new invite and subscription at recompiled.fyi.

Have a nice day!
- Will DePue
    '''
    writer_content = f'''\
Subject: Your subscription has been removed.

Alert:
You've missed two monthly writing periods in a row. This subscription will be permanently expired. You can always restart this subscription by asking a subscriber to issue a new invite or by signing up again at recompiled.fyi.

Have a nice day!
- Will DePue
    '''
    send_email(writer, writer_content) 
    for s in subs:
        send_email(s["email"], inviter_content)
    wdb.update_one({'email': writer },{'$set': {'expired': True}})
    cancel_all_subs(writer)
    # cancel_vendor_account(writer)

def check_code(code, sender):
    print("check_code", code, sender)
    c = wdb.find_one({ "email": sender, "secret_code": code })
    # print(c, bool(c))
    return bool(c)

def send_error_email(sender):
    print("send_error_email", sender)
    error_content = f'''\
Subject: Oops, that didn't work.

We just got an email from you to dispatch at Recompiled but it didn't look right. Double check that you included your writer code (you can check on our website) or that you sent from the right email account. It should look like XXX12345678XXX where the middle numbers are your code (include it anywhere in the email, we'll find it).

Remember, you can always check your writer code at https://recompiled.fyi/login

If this wasn't you, reply to this email and we'll make sure your account is secure.

Have a nice day!
- Will DePue
    '''
    send_email(sender, error_content) 

def dispatch_email(sender, subject, content):
    print("dispatch_email", sender, subject, content)
    sub_content = f'''\
Subject: {subject}

{content}

Content from {sender}.
This is from a Recompiled email list. 
Cancel this paid subscribtion our website @ recompiled.fyi.
    '''
    confirmation = f'''\
Subject: Your monthly newsletter has been dispatched!

Your email has been confirmed sent to your readers! Thanks for sending this month. We'll send payment over at the end of the month. :)

Have a nice day!
- Will DePue
    '''
    double_send = f'''\
Subject: You sent an extra letter this month!

Did you mean to send that letter now? We're going to send that email to your readers now, but this session ends oYour email has been confirmed sent to your readers! We'll send payment over at the end of the month. :)

Have a nice day!
- Will DePue
    '''    
    now = datetime.now()
    c = wdb.find_one({ "email": sender })
    a = adb.find_one({ "admin": True })
    if c["last_send_date"] > a["last_paid"] + timedelta(days=30):
        send_email(sender, double_send)
    for x in c["subscribers"]:
        send_email(x["email"], sub_content) 
    wdb.update_one({'email': sender },{'$set': {'last_send_date': datetime.now()}})
    send_email(sender, confirmation)

def cancel_all_subs(writer):
    r = wdb.find_one({ "email": writer })
    for s in r["subscribers"]:
            print(s["transaction_id"])
            try:
                stripe.Subscription.delete(s["transaction_id"])
            except:
                print("Failed to cancel sub for", s["transaction_id"])


def payout_user_email(writer):
    print("payout_user_email", writer)
    confirmation = f'''\
Subject: You just got paid!

You've been paid for this month - keep up the good work :) Visit your Stripe account for more information via Stripe login.

Have a nice day!
- Will DePue
    '''
    send_email(writer, confirmation)

def cancel_vendor_account(writer):
    print("cancel_vendor_account", writer)
    w = wdb.find_one({"email": writer})
    try:
        stripe.Account.delete(w["account_id"])
    except:
        print("Deleted account", w["account_id"])
    

def check_payout_users():
    now = datetime.now()
    a = adb.find_one({"admin": True})
    print("Check Payout Users", (a["last_paid"] < (now - timedelta(days=30))))
    if (a["last_paid"] < (now - timedelta(days=30))):
        adb.update_one(
            {'admin': True },
            {'$set': {'last_paid': now.replace(microsecond=0)}})
        ws = wdb.find({ "expired": False, "accepted": True }) 
        for w in ws:
            if w["last_send_date"] > (now - timedelta(days=30)): # i need to go through this logic more
                amount = 300 * len(w["subscribers"])
                print("Payout for", w["email"], amount)
                destination = w["account_id"]
                print(amount, destination)
                transfer = stripe.Transfer.create(
                    amount=amount,
                    currency="usd",
                    destination=destination,
                )
                payout_user_email(w["email"])
            else:
                print("Strike for", w["email"])
                if get_strikes(w["strikes"]) == 0:
                    warning_email(w["email"], w["subscribers"])
                else:
                    expire_subscription(w["email"], w["subscribers"])
                wdb.update_one({'email': w["email"] },{'$push': {'strikes': now}}) 


@sched.scheduled_job('cron', seconds=3)
def timed_job():
    cron_job()

sched.start()

# inter=setInterval(10,cron_job)
# print('Start time : {:.1f}s'.format(time.time()-StartTime))
# cron_job()
# # check_payout_users()