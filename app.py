from flask import (Flask, request, url_for, jsonify, render_template, abort)
import os
import requests
from colorama import Fore, Back, Style, init
import itertools
import re
from time import strftime, strptime
from datetime import datetime
from collections import namedtuple

app = Flask(__name__)
Range = namedtuple('Range', ['start', 'end'])

init(autoreset=True)

def replace_roman_numerals(string):
    return re.sub("(?i)\\bM{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})\\b",
        lambda m: m.group(0).upper(), string)

def format_course_title(section):
    return replace_roman_numerals(section['CourseTitle'].title())

def make_api_query(**kwargs):
    url = 'http://data.adicu.com/courses'
    params = kwargs
    params['api_token'] = app.DATA_ADICU_COM_API_KEY
    
    print 'Making API query to %s' % url
    results = requests.get(url, params=params)
    return results.json()

def make_fake_section_from_busy_time(busy_time):
    return {
        'MeetsOn1': busy_time[0:1],
        'StartTime1': busy_time[1:6] + ':00',
        'EndTime1': busy_time[7:12] + ':00'
    }

def parse_meeting_times(section):
    '''
    Transform the course times to
    [
        Range(start=datetime1, end=datetime1),
        Range(start=datetime2, end=datetime2),
        ...
    ]
    '''

    mt = []

    for i in range(1, 7):
        meetsOn_key = 'MeetsOn' + str(i)
        if meetsOn_key in section and section[meetsOn_key]:
            for day in section[meetsOn_key]:
                st = strptime(section['StartTime' + str(i)], "%H:%M:%S")
                sdt = datetime(
                    year=1970,
                    month=1,
                    day=4 + app.COLUMBIA_DAYS.index(day),
                    hour=st[3],
                    minute=st[4],
                    second=st[5]
                )

                et = strptime(section['EndTime' + str(i)], "%H:%M:%S")
                edt = datetime(
                    year=1970,
                    month=1,
                    day=4 + app.COLUMBIA_DAYS.index(day),
                    hour=et[3],
                    minute=et[4],
                    second=et[5]
                )

                mt.append(Range(start=sdt, end=edt))

    return mt

def section_combinations(courses, in_api_format=True):
    sn = []
    for c, v in courses.iteritems():
        section_names = []
        if in_api_format:
            for i in v['data']:
                section_names.append(i['Course'])
        else:
            for i in v:
                section_names.append(v[i]['Course'])

        sn.append(section_names)

    return list(itertools.product(*sn))

def ranges_overlap(range1, range2):
    latest_start = max(range1.start, range2.start)
    earliest_end = min(range1.end, range2.end)
    dt = earliest_end - latest_start
    if dt.days * 86400 + dt.seconds > 0:
        return True   # Ranges overlap
    else:
        return False  # Ranges don't overlap

def sections_conflict(section1, section2):
    for i in range(1, 7):
        mt1 = parse_meeting_times(section1)
        mt2 = parse_meeting_times(section2)
        iter = itertools.product(mt1, mt2)
        for a, b in iter:
            if ranges_overlap(a, b):
                return True

    return False

def bulletin_url_for_section(section):
    trailing_part = re.sub("([A-Za-z ]+)([0-9 ]+)([A-Za-z]+)([0-9]+)",
        "\\1/\\3\\2-"+section['Term']+"-\\4", section['Course'])
    return 'http://www.columbia.edu/cu/bulletin/uwb/subj/' + trailing_part

@app.route('/')
def hello():
    return render_template('index.html')

@app.route('/search.json')
def search():
    term = request.args.get('term')
    query = request.args.get('query')
    
    if term and query:
        query = re.sub("\\b([A-Za-z ]{4})([A-Za-z])([0-9 ]+)\\b", "\\1\\3\\2", query)

        data = []
        course_ids = []

        for criterion in ['course', 'title', 'description']:
            kwargs = {'term': term, criterion: query}
            results = make_api_query(**kwargs)

            if results['status_code'] != 200:
                continue

            for course in results['data']:
                course_id = re.sub("([A-Za-z ]+)([0-9 ]+)([A-Za-z])([0-9]+)",
                    "\\1\\3\\2", course['Course'])
                if course_id not in course_ids:
                    title = format_course_title(course)
                    description = course['Description']
                    if description and len(description) > app.COURSE_DESCRIPTION_MAX_LENGTH:
                        description = description[:app.COURSE_DESCRIPTION_MAX_LENGTH] + '&hellip;'

                    course_ids.append(course_id)
                    data.append({
                        'title': course_id + ' &bull; ' + title,
                        'subtitle': description,
                        'value': course_id
                    })
        
        return jsonify({
            'results': data
        })
    else:
        print 'Invalid parameters term=%s, query=%s' % (term, query)
        abort(400)  # Bad request

@app.route('/courses.json')
def courses():
    term = request.args.get('term')
    courses = request.args.get('courses')
    
    if term and courses:
        courses = courses.split(',')
        results = {c: make_api_query(term=term,
            course=re.sub("\\b([A-Za-z ]{4})([A-Za-z])([0-9 ]+)\\b",
            "\\1\\3\\2", c)) for c in courses if c}

        status_codes_OK = [v['status_code'] == 200 for v in results.values()]

        if not all(status_codes_OK):
            print 'API server returned errors'
            abort(400)  # Bad request

        data = {}
        for key in results.keys():
            data[key] = {course['Course']: course for course in results[key]['data']}

        return jsonify({
            'combinations': section_combinations(results),
            'course_data': data,
        })
    else:
        print 'Invalid parameters term=%s, courses=%s' % (term, courses)
        abort(400)  # Bad request

@app.route('/sections.html')
def sections():
    term = request.args.get('term')
    course = request.args.get('course')
    
    if term and course:
        course = re.sub("\\b([A-Za-z ]{4})([A-Za-z])([0-9 ]+)\\b", "\\1\\3\\2", course)
        results = make_api_query(term=term, course=course)

        if results['status_code'] != 200:
            print 'API server returned errors'
            abort(400)  # Bad request

        sections = []
        context = {'sections': sections}
        for section in results['data']:
            if 'course_id' not in context:
                course_id = re.sub("([A-Za-z ]+)([0-9 ]+)([A-Za-z])[0-9]+",
                    "\\1\\3\\2", section['Course'])
                context['course_id'] = course_id
                context['course_id_and_title'] = course_id + ' &bull; ' + \
                    format_course_title(section)

            data = {}
            data['id'] = int(re.sub("[A-Za-z ]+[0-9 ]+[A-Za-z]([0-9]+)",
                "\\1", section['Course']))
            data['full_id'] = section['Course']
            data['bulletin_url'] = bulletin_url_for_section(section)
            data['call_number'] = section['CallNumber']

            professors = []
            for i in range(1, 5):
                instructor_key = 'Instructor' + str(i) + 'Name'
                if instructor_key in section and section[instructor_key]:
                    professor = section[instructor_key]
                    professor = ' '.join(reversed(professor.split(', '))).title()
                    professors.append(professor)

            data['professors'] = ", ".join(professors)

            meetings = []
            for i in range(1, 7):
                meetsOn_key = 'MeetsOn' + str(i)
                if meetsOn_key in section and section[meetsOn_key]:
                    start = strftime("%I:%M %p",
                        strptime(section['StartTime' + str(i)], "%H:%M:%S"))
                    if start[0] == '0':
                        start = start[1:]

                    end = strftime("%I:%M %p",
                        strptime(section['EndTime' + str(i)], "%H:%M:%S"))
                    if end[0] == '0':
                        end = end[1:]

                    meetings.append({
                        'meetsOn': section[meetsOn_key],
                        'startTime': start,
                        'endTime': end
                    })

            data['meetings'] = meetings

            sections.append(data)

        sections.sort(key=lambda section: section['id'])

        return render_template('sections.html', **context)
    else:
        print 'Invalid parameters term=%s, course=%s' % (term, course)
        abort(400)  # Bad request

@app.route('/events.json')
def events():
    term = request.args.get('term')
    busy_times = request.args.getlist('busyTimes[]')
    sections = request.args.getlist('sections[]')

    if term and len(sections):
        if len(busy_times):
            busy_times = [make_fake_section_from_busy_time(busy_time) for busy_time in busy_times]
        
        courses = {}
        for section in sections:
            course_id = section[:-3];
            if course_id in courses:
                course_list = courses[course_id];
            else:
                course_list = []
                courses[course_id] = course_list
            course_list.append(section)

        results = {c: make_api_query(term=term,
            course=c) for c in courses.keys() if c}

        status_codes_OK = [v['status_code'] == 200 for v in results.values()]

        if not all(status_codes_OK):
            print 'API server returned errors'
            return ''

        # Filter out undesired sections
        data = {}
        for key in results.keys():
            data[key] = {course['Course']: course for course in results[key]['data'] if course['Course'] in courses[course['Course'][:-3]]}

        # Filter out conflicting combinations of classes
        valid_combinations = []
        for combination in section_combinations(data, False):
            is_valid = True
            if len(busy_times):
                for section_name, busy_time in itertools.product(combination, busy_times):
                    section = data[section_name[:-3]][section_name]
                    if sections_conflict(section, busy_time):
                        is_valid = False
                        break

                if not is_valid:
                    continue

            for a, b in itertools.combinations(combination, 2):
                if sections_conflict(a, b):
                    is_valid = False
                    break

            if is_valid:
                valid_combinations.append(combination)

        busy_time_events = []
        event_combinations = []
        events = {
            'busyTimes': busy_time_events,
            'eventLists': event_combinations
        }

        if len(busy_times):
            for bt_section in busy_times:
                mt = parse_meeting_times(bt_section)[0]
                busy_time_events.append({
                    'start': mt.start.isoformat(),
                    'end': mt.end.isoformat(),
                    'title': 'Unavailable'
                })

        for combination in valid_combinations:
            calendar_events = []
            for section_name in combination:
                section = data[section_name[:-3]][section_name]
                title = format_course_title(section) + ' (#' + str(int(section_name[-3:])) + ')'
                url = bulletin_url_for_section(section)
                meeting_times = parse_meeting_times(section)
                for meeting_time in meeting_times:
                    calendar_events.append({
                        'start': meeting_time.start.isoformat(),
                        'end': meeting_time.end.isoformat(),
                        'url': url,
                        'title': title,
                        'editable': False,
                        'backgroundColor': '#3366CC'
                    })

            event_combinations.append(calendar_events)

        return jsonify(events)
    else:
        print 'Invalid parameters term=%s, busyTimes=%s, sections=%s' % (term, busy_times, sections)
        return ''

if __name__ == '__main__':
    app.COLUMBIA_DAYS = "UMTWRFS"
    app.COLUMBIA_DAYS_DICT = {
        "U": "Sunday",
        "M": "Monday",
        "T": "Tuesday",
        "W": "Wednesday",
        "R": "Thursday",
        "F": "Friday",
        "S": "Saturday"
    }
    app.COURSE_DESCRIPTION_MAX_LENGTH = 600
    app.DATA_ADICU_COM_API_KEY = os.environ.get('DATA_ADICU_COM_API_KEY')

    # Bind to PORT if defined, otherwise default to 5000.
    port = int(os.environ.get('PORT', 5000))

    if os.environ.get('DEBUG'):
        print Fore.RED, 'Running in DEBUG mode!'
        app.debug = True

    app.run(host='0.0.0.0', port=port)
