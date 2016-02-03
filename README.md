# Conference Organization Application

## Description

This project is a cloud-based API server used to support a conferenAce organization application that exists on the web. The API supports the following functionality:

- User authentication
- User profiles
- Conference information
- Conference Session information
- Wishlist for user
- Various data queries and 
- Memcache entries

Hosted on Google's Cloud Platform this application is scalable to hundreds of thousands of users.

The location of the application is [fswdn4-dd.appspot.com](https://fswdn4-dd.appspot.com).  APIs can be accessed via the API Explorer [here](https://apis-explorer.appspot.com/apis-explorer/?base=https://fswdn4-dd.appspot.com/_ah/api#p/) 

## Setup Instructions
1. Download and install the [Google App Engine SDK for Python](https://cloud.google.com/appengine/downloads?hl=en#Google_App_Engine_SDK_for_Python)
2. Clone this repository.
3. In Google App Engine, select 'File->Add Existing Application...' and set the Application Path to the root directory of this repository.
3. Update the value of `application` in `app.yaml` to the app ID you have registered in the App Engine admin console and would like to use to host your instance of this sample.
4. Update the values at the top of `settings.py` to reflect the respective client IDs you have registered in the [Developer Console][4].
5. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
6. (Optional) Mark the configuration files as unchanged as follows: `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
7. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
8. (Optional) Generate your client library(ies) with [the endpoints tool][6].
9. Deploy your application.

## Project Tasks

This project contains tasks to improve the functionality of the basic conference system.  Improvements are broken into 4 separate tasks:

- Task 1: Add Sessions to a Conference
- Task 2: Add Sessions to a Wishlist
- Task 3: Work on Indexes and Queries
- Task 4: Add a Task - Featured Speaker

###  Task 1: Add Sessions to a Conference

  This task included creating a number of Endpoint methods as well as entities for a Session and a Speaker

#### Endpoints Methods

The following Endpoints were created for this task

- getConferenceSessions: Return all sessions for one conference
- getConferenceSessionsByType: For one conference, return all sessions of a specified type
- getSessionsBySpeaker: For one speaker, return all sessions given by this speaker across all conferences
- createSession: Create a session for a conference.  Sessions can only be created by the orangizer of the conference

#### Entities

##### Session

| Property      | Type             | 
|---------------|------------------| 
| name          | string, required | 
| highlights    | string           | 
| speaker       | string, required | 
| duration      | integer          | 
| typeOfSession | string           | 
| date          | date             | 
| startTime     | time             | 

Parent: Conference Key that the Session belongs to

##### Speaker

| Property           | Type             |
| ------------------ | ---------------- |
| name               | string, required |
| email              | string           |
| sessionKeysToSpeak | string, repeated |   

Parent: None


#### Notes

- Conference Key was chosen as the Parent for a Session because of the strong relationship between conferences and sessions.  In addition, retrieving conference details for a session will be quite common and this implementation allows us to use the ancestor path to retrieve this information

- Name and Speaker are required fields.  Name is the basic info required to identify a session and the speaker is requried to create speaker entries

- The Speaker entity has no parent.  The reason for this choice is that no other entity has a what I believe is a 'real' parent-child relationship with a speaker.  I could have made the profile a parent but that would imply that all speakers require an account to be setup and that didn't make sense.  I also thought about using conferences but since a speaker can speak at many conferences it didn't make sense to create a parent of just one conference. 

- Speakers are created when a sessions are created.  When a session is created a check is made against the speaker entity to see if the speaker already exists.  If the speaker exists then the session key is added to the sessionKeysToSpeak list, otherwise, a new speaker is created and the session key becomes the first entty of the sessionKeysToSpeak list.

- Unique speakers are identified by their name.  Keys are generated using the speaker name as input ensuring that "John Smith", for example, does not appear in the speaker entity twice.  This could be an issue if someone lists "John Smith" as "John A. Smith" or "Johnny Smith" but that is an acceptable limitation.

- Additional Enpoint: 'updateSpeaker' can be used to update additional speaker information

### Task 2: Add Sessions to a Wishlist

  For this task users have a wishlist.  A user can use this wishlist to mark sessions that they are interested, rerieve the list and delete sessions from the wishlist.  This required a few more Endpoint functions and I also implemented the wishlist by creating another entity.

#### Endpoints Methods

The following methods were created for this task:

- addSessionToWishlist: Given a session key, this method will add the session to the user wishlist
- getSessionsInWishlist: Retireves all sessions in the user's wishlist
- deleteSessionInWishlist: Given a session key, this method will remove the session from the wishlist

#### Entities

##### Wishlist

| Property           | Type             |
| ------------------ | ---------------- |
| websafeSessionKey  | string           |
| sessionName        | string           |
| userId             | string           |
| duration           | integer          |   


### Task 3: Work on Indexes and Queries

This task required a few steps:

- Ensure the indexes are supported for all queries required by the Endppints methods
- Create two new queries
- Solve a defined query problem (no workshops and no sessions after 7pm)

#### Index support

  index.yaml file was reviewed and all required composite queries are in the file

#### Create two new queries

- Query #1: getSessionsGreaterThanDate.  Returns all sessions with a date greater than or equal to the specified date

- Query #2: getWishlistSessionsLongerThanDuration. Return all wishlist entries where the session duration is equal to or longer than the specified duration

#### Solve a defined query problem

  The query I was asked to handle was "You don't like workshops and you don't like sessions after 7pm".
  
  When attempting to create and execute this query I received an error.  "BadRequestError: Only one inequality filter per query is supported."  This query contained two inequality filters, startTime and typeOfSession.  

  To get around this issue I first used ndb to return a list of sessions before 7pm.  I then used python to filter out sessions that were defined as "Workshop".  

### Task 4: Add a Task - Featured Speaker

  For this task logic was added to the createSession Endpoint to check if the speaker was speaking at any other sessions in the conference.  If this is true then a new Push Queue task job was addedd to create a Memcache entry with the key 'CONFERENCE\_FEATURED\_SPEAKERS'.
  
  The task url is '/tasks/set_featured_speaker'.

  In addition, a new Endpoint was created: 'getFeaturedSpeaker' which returns the featured speaker, if there is one.     

## Resources

[App Engine][1]

[Python][2]

[Google Cloud Endpoints][3]

[Developer Console][4]

[localhost:8080][5]

[Google Cloud Endpoints Tool][6]


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool

