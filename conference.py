#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21
updated by David D on 2016 jan 24

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import Wishlist
from models import WishlistForm
from models import WishlistForms
from models import Speaker
from models import SpeakerMiniForm
from models import SpeakerForm
from models import SpeakerForms

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId


EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
MEMCACHE_CONFERENCE_FEATURED_SPEAKERS_KEY = "CONFERENCE_FEATURED_SPEAKERS"
FEATURED_SPEAKERS_TPL = ('Featured Speaker for conference: %s is %s! '
                         'Sessions include: %s')

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_TYPE_GET_REQUEST = endpoints.ResourceContainer(
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
)

SESSION_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    speaker=messages.StringField(1),
)

SESSION_DATE_GET_REQUEST = endpoints.ResourceContainer(
    date=messages.StringField(1),
)

WISHLIST_GET_REQUEST = endpoints.ResourceContainer(
    websafeSessionKey=messages.StringField(1),
)

WISHLIST_DURATION_GET_REQUEST = endpoints.ResourceContainer(
    duration=messages.IntegerField(1, variant=messages.Variant.INT32),
)

SPEAKER_POST_REQUEST = endpoints.ResourceContainer(
    SpeakerMiniForm,
    websafeSpeakerKey=messages.StringField(1),
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )

# - - - Profile objects - - - - - - - - - - - - - - - - - - -


    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

# - - - Session object - - - - - - - - - - - - - - - - - - - -


    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                
                # convert Date and Start Time to date string; just copy others
                if field.name == "date" or field.name == "startTime":
                    setattr(sf, field.name, str(getattr(sess, field.name)))
                else:
                    setattr(sf, field.name, getattr(sess, field.name))
            elif field.name == "confWebsafeKey":
                setattr(sf, field.name, sess.key.parent().urlsafe())
            elif field.name == "websafeKey":
                setattr(sf, field.name, sess.key.urlsafe())
        
        sf.check_initialized()
        return sf

    def _createSessionObject(self, request):
        """Create Session object, returning SessionForm/request."""
        
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")

        if not request.speaker:
            raise endpoints.BadRequestException("Session 'speaker' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        
        # convert date from string to Date object;
        if data['date']:
            data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
        
        # convert startTime from string to Time object;
        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'][:5], "%H:%M").time()

        # use the Conference Key to generate a ID for the Session
        # create Session key with Conference as the parent
        c_key = ndb.Key(urlsafe=data['confWebsafeKey'])
        s_id = Session.allocate_ids(size=1, parent=c_key)[0]
        s_key = ndb.Key(Session, s_id, parent=c_key)
        
        # assign the Session key
        data['key'] = s_key
        
        del data['confWebsafeKey']
        del data['websafeKey']
        
        # find a Speaker record 
        sp_key = ndb.Key(Speaker, data['speaker'])
        speaker = sp_key.get()
        
        # create new Speaker if not there
        if not speaker:
            speaker = Speaker(
                key = sp_key,
                name = data['speaker'],
            )

        # append the Session that the Speaker will speak
        speaker.sessionKeysToSpeak.append(s_key.urlsafe())
        
        # create Session & create/update Speaker
        Session(**data).put()
        speaker.put()
        
        # Check number of sessions for the Speaker to see if the
        # MEMCACHE entry should be updated
        num_speaker_sessions = Session.query(ancestor=c_key). \
            filter(Session.speaker==data['speaker']). \
            count(limit=None)
        
        # A num_speaker_sessions count greater than 1 will update the
        # MEMCACHE 
        if num_speaker_sessions > 1:
            
            # This query...all sessions for the conference where this
            # speaker is speaking
            speaker_sessions = Session.query(ancestor=c_key). \
                filter(Session.speaker==data['speaker']). \
                fetch()
            conf = c_key.get()

            # update the MEMCACHE entry
            speaker_announcement = FEATURED_SPEAKERS_TPL % (
                conf.name, data['speaker'], ', '.join(speaker_session.name for speaker_session in speaker_sessions))
            memcache.set(MEMCACHE_CONFERENCE_FEATURED_SPEAKERS_KEY, speaker_announcement)            

        return request


    @endpoints.method(SessionForm, SessionForm, path='session',
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        
        p_key = ndb.Key(Profile, user_id)
        
        conf = ndb.Key(urlsafe=request.confWebsafeKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        
        # Logged in user can only create Sessions for Conferences they created
        if conf.key.parent() != p_key:
            raise endpoints.UnauthorizedException('Unauthorized Access')

        return self._createSessionObject(request)


    @endpoints.method(CONF_GET_REQUEST, SessionForms,
            path='getConferenceSessions/{websafeConferenceKey}',
            http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return sessions for a conference."""
        
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        
        # create ancestor query for all key matches for this conference
        sessions = Session.query(ancestor=c_key)
        
        # return set of SessionForm objects per session
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in sessions]
        )
        
    @endpoints.method(SESSION_TYPE_GET_REQUEST, SessionForms,
            path='getConferenceSessionsByType',
            http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Find specific type of sessions for a conference """
        
        c_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        q = Session.query(ancestor=c_key). \
                filter(Session.typeOfSession==request.typeOfSession)
                
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in q]
        )


    @endpoints.method(SESSION_SPEAKER_GET_REQUEST, SessionForms,
            path='getSessionsBySpeaker',
            http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Find all sessions by the speaker across all conferences"""
        
        q = Session.query(Session.speaker==request.speaker)
        
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in q]
        )


    @endpoints.method(SESSION_DATE_GET_REQUEST, SessionForms,
            path='getSessionsGreaterThanDate',
            http_method='GET', name='getSessionsGreaterThanDate')
    def getSessionsGreaterThanDate(self, request):
        """Find all sessions greater than or equal to specified date across all conferences"""
        
        if request.date:
            startDate = datetime.strptime(request.date[:10], "%Y-%m-%d").date()
        
        q = Session.query(Session.date>=startDate).order(Session.date)
        
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in q]
        )

    @endpoints.method(message_types.VoidMessage, SessionForms, 
        path='wishlist/getSessionsNoWorkshopBefore7',
        http_method='GET', name='getSessionsNoWorkshopBefore7')
    def getSessionsNoWorkshopBefore7(self, request):
        """Return all sessions that are not workshops AND
           before 7pm (19:00)"""
        
        # create time object for 19:00 (7pm) and get all sessions
        # before 19:00
        startTime = datetime.strptime("19:00", "%H:%M").time()
        
        q = Session.query(Session.startTime<=startTime)
        
        # filter the results to exclude any workshops and return
        # the rest
        return SessionForms(
            items=[self._copySessionToForm(sess) for sess in q if sess.typeOfSession != "Workshop"]
        )

# - - Wishlist object - - - - - - - - - - - - - - - - - - - - -

    def _copyWishlistToForm(self, wishList):
        """Copy relevant fields from Wishlist to WishlistForm."""
        wf = WishlistForm()
        for field in wf.all_fields():
            if hasattr(wishList, field.name):
                setattr(wf, field.name, getattr(wishList, field.name))
            elif field.name == "websafeKey":
                setattr(wf, field.name, wishList.key.urlsafe())
        
        wf.check_initialized()
        return wf

    def _updateWishlistObject(self, request, reg=True):
        """Create or update Wishlist object, returning WishlistForm/request."""
        
        # preload necessary data items
        retval = None
        
        # as usual make sure user is logged in
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        
        user_id = getUserId(user)

        if reg:
            data = {field.name: getattr(request, field.name) for field in request.all_fields()}
            
            sess  = ndb.Key(urlsafe=request.websafeSessionKey).get()
            p_key = ndb.Key(Profile, user_id)
            
            q = Wishlist.query(ancestor=p_key). \
                    filter(Wishlist.websafeSessionKey==request.websafeSessionKey).get()

            if q:
                raise endpoints.BadRequestException('Session already in Wishlist')

            data['sessionName'] = sess.name
            data['userId'] = user_id
            data['duration'] = sess.duration
            
            # create Wishlist key with logged in user as the parent
            w_id = Wishlist.allocate_ids(size=1, parent=p_key)[0]          
            w_key = ndb.Key(Wishlist, w_id, parent=p_key)
            
            data['key'] = w_key
            
            # create Wishlist entry
            Wishlist(**data).put()
            retVal = True

        else:
            # Find the Wishlist Session record for the user
            q = Wishlist.query(ancestor=ndb.Key(Profile, user_id)). \
                filter(Wishlist.websafeSessionKey==request.websafeSessionKey). \
                get() 
            
            # only if it is found should we delete it 
            if q:
                q.key.delete()
                retVal = True
            else:        
                retVal = False  

        return BooleanMessage(data=retval)
        
 
    @endpoints.method(WISHLIST_GET_REQUEST, BooleanMessage, path='wishlist',
            http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Create new wishlist entry."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        
        p_key = ndb.Key(Profile, user_id)
        
        #  make sure a Session key is supplied
        if not request.websafeSessionKey:
            raise endpoints.BadRequestException("websafeSessionKey field required")
        
        # make sure we can find the Session key.  If it is bogus then 
        # tell the user it does not exist
        try:
            s_key = ndb.Key(urlsafe=request.websafeSessionKey)
        except Exception, e:
            raise endpoints.NotFoundException ("invalid websafeSessionKey")
        
        sess = s_key.get()

        # make sure we can get the session and if so, then we can do the update
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.websafeSessionKey)
        
        return self._updateWishlistObject(request)


    @endpoints.method(WISHLIST_GET_REQUEST, BooleanMessage, path='wishlist',
            http_method='DELETE', name='deleteSessionInWishlist')
    def deleteSessionToWishlist(self, request):
        """Delete wishlist entry."""
        
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        
        p_key = ndb.Key(Profile, user_id)
        
        #  make sure a Session key is supplied
        if not request.websafeSessionKey:
            raise endpoints.BadRequestException("websafeSessionKey field required")
        
        # make sure we can find the Session key.  If it is bogus then 
        # tell the user it does not exist
        try:
            s_key = ndb.Key(urlsafe=request.websafeSessionKey)
        except Exception, e:
            raise endpoints.NotFoundException ("invalid websafeSessionKey")

        sess = s_key.get()
        
        # make sure we can get the session and if so, then we can do the update
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.websafeSessionKey)
        
        return self._updateWishlistObject(request, reg = False)
    
    @endpoints.method(message_types.VoidMessage, WishlistForms, 
        path='wishlist/sessions',
        http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get all Wishlist entries for the user."""
        
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        
        # create ancestor query for all key matches for this user
        wishlist = Wishlist.query(ancestor=ndb.Key(Profile, user_id))
        
        # return set of WishlistForm objects per Wishlist Entry
        return WishlistForms(
            items=[self._copyWishlistToForm(wish) for wish in wishlist]
        )


    @endpoints.method(WISHLIST_DURATION_GET_REQUEST, WishlistForms, 
        path='wishlist/getWishlistSessionsLongerThanDuration',
        http_method='GET', name='getWishlistSessionsLongerThanDuration')
    def getWishlistSessionsLongerThanDuration(self, request):
        """Locate all wishlist entries where the duration is equal or longer
           than the requested duration"""
        
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        
        # create ancestor query for all key matches for this user
        # and the duration is longer or equal to the specified duration
        wishls = Wishlist.query(ancestor=ndb.Key(Profile, user_id)). \
                     filter(Wishlist.duration>=request.duration)

        # return set of WishlistForm objects per Wishlist
        return WishlistForms(
            items=[self._copyWishlistToForm(wish) for wish in wishls]
        )


# - - Speaker object - - - - - - - - - - - - - - - - - - - - 

    def _copySpeakerToForm(self, speaker):
        """Copy relevant fields from Speaker to SpeakerForm."""
        
        # copy relevant fields from Speaker to SpeakerForm
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, speaker.key.urlsafe())
        
        sf.check_initialized()
        return sf

    def _updateSpeakerObject(self, request):
        """Update Speaker object, returning SpeakerForm/request."""
        
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy SpeakerForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # get existing speaker.....
        speaker = ndb.Key(urlsafe=request.websafeSpeakerKey).get()
        
        # ....and check that speaker exists
        if not speaker:
            raise endpoints.NotFoundException(
                'No speaker found with key: %s' % request.websafeSpeakerKey)

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from SpeakerForm to Speaker object
        for field in request.all_fields():
            data = getattr(request, field.name)
            
            # only copy fields where we get data
            if data not in (None, []):
                setattr(speaker, field.name, data)
        
        speaker.put()
        
        return self._copySpeakerToForm(speaker)

    @endpoints.method(SPEAKER_POST_REQUEST, SpeakerForm,
            path='speaker/{websafeSpeakerKey}',
            http_method='PUT', name='updateSpeaker')
    def updateSpeaker(self, request):
        """Update speaker w/provided fields & return w/updated info."""
        return self._updateSpeakerObject(request)
 
    @endpoints.method(message_types.VoidMessage, SpeakerForms,
            path='getSpeakers',
            http_method='GET', name='getSpeakers')
    def getSpeakers(self, request):
        """Return all speakers."""
        
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        
        speakers = Speaker.query()
        
        return SpeakerForms(
            items=[self._copySpeakerToForm(speak) for speak in speakers]
        )

    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/featured_speaker/get',
            http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_CONFERENCE_FEATURED_SPEAKERS_KEY) or "")


api = endpoints.api_server([ConferenceApi]) # register API
