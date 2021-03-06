import os
from flask import Flask, request, render_template, session, redirect, jsonify, flash
from jinja2 import StrictUndefined
from twilio.rest import Client
import schedule
import time
import datetime
import arrow
import phonenumbers
from threading import Thread
from helper import *
from model import (connect_to_db, db, User, CreateHabit, UserHabit, Success,
    Streak, Partner, Coach, UserProfile, FactorScore, Factor,
    FactorHabitRating)

app = Flask(__name__)
app.secret_key = 'ABCSECRETDEF'

# raise errors if there are undefined variables in Jinja2
app.jinja_env.undefined = StrictUndefined

start_time = time.time()


@app.route('/', methods=['POST'])
def track_success():
    """ process requests from API.AI triggered on success events. Process & 
    store in success table """

    # collect json from request, convert to dictionary:
    habot_request = request.get_json()

    print "habot request received from Dialogflow"
    print habot_request

    # extract user mobile - ex: +18028253270 and format it for lookup in db
    mobile_unprocessed = habot_request['originalRequest']['data']['From']
    country_code = habot_request['originalRequest']['data']['FromCountry']
    mobile = format_mobile(mobile_unprocessed, country_code)
    user_id = get_user_id(mobile)

    #parse habot_request
    if habot_request['result']['action'] == 'track_success':
        
        # extract time (time API.ai agent tracks success) - ex: 2017-10-01T15:40:08.959Z
        success_time = arrow.get(habot_request['timestamp'])
        
        # track success
        success = process_success(user_id, success_time)

        # generate success message
        msg = congrats_msg(user_id)
        print "success lodged"
        # if already success on this day, send alt message. 
        if not success:
            msg ="I've already tracked a success for you today, but I'm thrilled to hear you were successful again. Keep up the great work."
        
        # send success message
        resp = { "Content-type" : "application/json", 
        "speech": msg, 
        "displayText": msg,
        "data": {},
        "contextOut":[],
        "source": "haBot app"}

        response = jsonify(resp)

    elif habot_request['result']['action'] == 'unpause':

        msg = unpause_habit(user_id)

        resp = { "Content-type" : "application/json", 
        "speech": msg, 
        "displayText": msg,
        "data": {},
        "contextOut":[],
        "source": "haBot app"}

        response = jsonify(resp)

    print response
   
    return response

@app.route('/home')
def show_homepage():
    """Show homepage."""

    sorted_countries = sort_countries()

    default_country = 'US'

    return render_template('homepage.html', sorted_countries=sorted_countries, default_country=default_country)


@app.route('/verify', methods=['GET', 'POST'])
def verify_user():
    """ ask user for verification code that was sent via sms """

    mobile = request.args.get('mobile')
    country_code = request.args.get('country_code')
    session['country_code'] = country_code
    session['mobile'] = format_mobile(mobile, country_code)

    send_confirmation_code(mobile)

    return render_template('verification.html')
    # form to collect code and verify


@app.route('/signin', methods=['POST', 'GET'])
def log_in_user():
    """verifies mobile number via twilio then checks where to send user"""

    code = request.args.get('code')
    # verify correct code with Twilio
    session['code'] = code
    mobile = session['mobile']

    # if correct code:
    if code == session['verification_code']:
        
        # query if user is in the db:
        user = User.query.filter(User.mobile == mobile).first()
        
        # if user in db, redirect to /dashboard
        if user != None:
            session['user_id'] = user.user_id
            return redirect('/dashboard')
        
        else:
            #redirect to onboarding flow
            return redirect('/timezone')
    
    # if incorrect code:
    else:
        flash("The code you entered was incorrect. Please enter the new code sent to you")
        return redirect('/verify?mobile={}'.format(mobile))

    # Later TODO: determine if they've completed onboarding, etc.

@app.route('/signup', methods=['POST', 'GET'])
def create_user():
    """creates user"""

    country_code = session['country_code']
    tz = session['tz']

    # format mobile for storing in DB accuratey
    mobile = format_mobile(session['mobile'], country_code)
    name = session['name']

    # create user and add user_id to session
    user_id = create_user_return_id(name, mobile, tz)

    # send user welcome msg
    send_welcome_msg(user_id)

    #set user_id in session
    session['user_id'] = user_id

    #set profile date & create a UserFactorProfile
    date = (arrow.utcnow()).format('YYYY-MM-DD HH:mm:ss ZZ')
    profile_id = create_profile_return_id(user_id, date)
    
    #create dictionary of results from factor rating to pass to create_factor_rating
    factors = Factor.query.all()
    factor_scores = {}
    for factor in factors:
        score = request.args.get('{}'.format(factor.factor_id))
        factor_scores[factor.factor_id] = int(score)    

    create_factor_scores(profile_id, factor_scores)

    
    return redirect('/habit')
 

@app.route('/dashboard')
def show_dashboard():
    """ display user progress"""

    user_id = session['user_id']

    # calculate and render data about current habit
    user_habit = UserHabit.query.filter(UserHabit.user_id == user_id, UserHabit.current == True).first()
    stats = get_stats(user_habit.habit_id)

    # calculate and render data about most recent factor scores
    last_factor_scores = get_last_factor_profile(user_id)

    return render_template('dashboard.html', user_habit=user_habit,
        stats=stats, last_factor_scores=last_factor_scores)

    
@app.route('/name')
def choose_name():
    """ onboarding, step 1 - identify name """

    tz = request.args.get('tz')
    session['tz'] = tz

    return render_template('name.html')
    # form to collect name --> send user to factors, but for now --> /habit


@app.route('/timezone', methods=['GET'])
def choose_timezone():
    """ onboarding, step 2 - identify timezone """
    
    country_code = session['country_code']

    country_code = session['country_code']
    
    timezones = get_country_timezones(country_code)

    return render_template('timezone.html', timezones=timezones)


@app.route('/factors')
def rate_factors():
    """ onboarding, step 3 - identify factors """

    name = request.args.get('name')
    session['name'] = name

    factors = Factor.query.all()

    return render_template('factors.html', factors=factors) 


@app.route('/habit')
def show_recommended_habits():
    """ show habits recommended to user based on user profile and habit ratings 
    for that profile"""

    return render_template('recommend.html')

@app.route('/get-recs.json')
def display_recommendations():
    """ show habits recommended to user based on user profile and habit ratings
    for that profile. Paginate by 4 via an ajax call"""

    user_id = session['user_id']

    ranked_habits = get_recommendations(user_id)
    length = len(ranked_habits)
    print "# of habits to recommend:" 
    print length
    
    # utilize a counter to paginate recs by 4 habits
    if 'rec_index' in session:

        index = session['rec_index']
        
        # if this request would provide the last recs in list, reset counter
        if ((index == length) or (index == (length - (length % 4)))):
            session['rec_index'] = 4

        # if not at end of list, increment the counter
        else:
            session['rec_index'] += 4
    else:
        # if it's the user's first request, create counter
        session['rec_index'] = 8
        index = 4

    print "rec_index: {}".format(session['rec_index'])
    print "index: {}".format(session['rec_index'])

    recs = ranked_habits[(index-4):index]

    recommendations = {}
    for rec in recs: 
        recommendations[rec.create_habit_id] = { "title" : rec.title, "description" : rec.description }

    return jsonify(recommendations)

@app.route('/get-stats.json')
def render_stats_graph():
    """ populate dashboard graph with stats - an ajax call """

    user_id = session['user_id']
    
    # calculate and render data about current habit
    user_habit = UserHabit.query.filter(UserHabit.user_id == user_id, UserHabit.current == True).first()
    
    stats = get_stats(user_habit.habit_id)

    graph_stats = get_graph_stats(stats)

    return jsonify(graph_stats)


@app.route('/habits')
def show_all_habits():
    
    habits = CreateHabit.query.all()

    return render_template('browse.html', habits=habits)


@app.route('/time', methods=['POST', 'GET'])
def define_habit_time():
    """ collects habit time from user"""

    # # if 'break' in session, display diff text (re: replacement)

    habit_id = int(request.args.get('habit_id'))
    #get reomended hour for habit
    habit = CreateHabit.query.filter(CreateHabit.create_habit_id == habit_id).first()
    hour = habit.hour

    session['habit_id'] = habit_id

    return render_template("time.html", hour=hour)


@app.route('/confirm', methods=['POST', 'GET'])
def show_confirmation():
    """ confirm information about habit to user """

    hour = int(request.args.get("hour"))
    user_id = session['user_id']
    user = User.query.filter(User.user_id == user_id).one()
    tz = user.tz

    # create an arrow object, replace hour and timezone, convert to UTC and format for db
    t = arrow.now()
    time = t.replace(hour=int('{}'.format(hour)), tzinfo='{}'.format(tz))
    utc_time = time.to('UTC')
    utc_time = utc_time.format('YYYY-MM-DD HH:mm:ss ZZ')
   
    create_habit_id = session['habit_id']
   
    if 'break_habit_id' not in session:
        break_habit_id = None
    else:
        break_habit_id = session['break_habit_id']
    if 'partner_id' not in session:
        partner_id = None
    else:
        partner_id = session['partner_id']

    habit_id = add_new_habit_return_id(user_id, create_habit_id, break_habit_id, utc_time, partner_id)

    habit = UserHabit.query.filter(UserHabit.habit_id == habit_id).first()
    send_habit_intro_msg(user_id)

    return render_template('confirm.html', habit=habit)



def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)




if __name__ == "__main__":
    # set the schedule for sending daily nudges:
    #schedule.every(1).minutes.do(send_daily_msg)
    
    # establish the thread:
    #t = Thread(target=run_schedule)
    #t.start()
    #print"Schedule is running. Start time: " + str(start_time)

    # connect to db
    connect_to_db(app)
    app.debug=True
    app.jinja_env.auto_reload=app.debug
    app.run(host="0.0.0.0", port=5000)
    
    # Use the DebugToolbar
    # DebugToolbarExtension(app)

    