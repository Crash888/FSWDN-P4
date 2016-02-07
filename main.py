#!/usr/bin/env python

"""
main.py -- Udacity conference server-side Python App Engine
    HTTP controller handlers for memcache & task queue access

$Id$

created by wesc on 2014 may 24

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import webapp2
from google.appengine.api import app_identity
from google.appengine.api import mail
from google.appengine.api import memcache
from google.appengine.ext import ndb

from models import Session

from conference import ConferenceApi

MEMCACHE_CONFERENCE_FEATURED_SPEAKERS_KEY = "CONFERENCE_FEATURED_SPEAKERS"
FEATURED_SPEAKERS_TPL = ('Featured Speaker for conference %s is %s! '
                         'Sessions include: %s')

class SetAnnouncementHandler(webapp2.RequestHandler):
    def get(self):
        """Set Announcement in Memcache."""
        ConferenceApi._cacheAnnouncement()
        self.response.set_status(204)


class SendConfirmationEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Conference creation."""
        mail.send_mail(
            'noreply@%s.appspotmail.com' % (
                app_identity.get_application_id()),     # from
            self.request.get('email'),                  # to
            'You created a new Conference!',            # subj
            'Hi, you have created a following '         # body
            'conference:\r\n\r\n%s' % self.request.get(
                'conferenceInfo')
        )


class SetFeaturedSpeakerHandler(webapp2.RequestHandler):
    def post(self):
        "Set Memcache Key for Featured Speaker"
        
        c_key = ndb.Key(urlsafe=self.request.get('conference_key'))
            
        num_speaker_sessions = Session.query(ancestor=c_key). \
            filter(Session.websafeSpeakerKey==self.request.get('speaker_key')). \
            count(limit=None)
        
        # A num_speaker_sessions count greater than 1 will update the
        # MEMCACHE 
        if num_speaker_sessions > 1:
            
            # This query...all sessions for the conference where this
            # speaker is speaking
            speaker_sessions = Session.query(ancestor=c_key). \
                filter(Session.websafeSpeakerKey==self.request.get('speaker_key')). \
                fetch()
            
            conf = c_key.get()
            
            sp_key = ndb.Key(urlsafe=self.request.get('speaker_key'))
            speaker = sp_key.get()        
            
            speaker_announcement = FEATURED_SPEAKERS_TPL % (
                conf.name, speaker.name, ', '.join(speaker_session.name for speaker_session in speaker_sessions))
        

            memcache.set(MEMCACHE_CONFERENCE_FEATURED_SPEAKERS_KEY, speaker_announcement)
        
        self.response.set_status(204)


app = webapp2.WSGIApplication([
    ('/crons/set_announcement', SetAnnouncementHandler),
    ('/tasks/send_confirmation_email', SendConfirmationEmailHandler),
    ('/tasks/set_featured_speaker', SetFeaturedSpeakerHandler),
], debug=True)
