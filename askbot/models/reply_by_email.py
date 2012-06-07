from datetime import datetime
import random
import string
import logging
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import ugettext_lazy as _
from askbot.models.post import Post
from askbot.models.base import BaseQuerySetManager
from askbot.conf import settings as askbot_settings
from askbot.utils import mail

class ReplyAddressManager(BaseQuerySetManager):
    """A manager for the :class:`ReplyAddress` model"""

    def get_unused(self, address, allowed_from_email):
        return self.get(
            address = address,
            allowed_from_email = allowed_from_email,
            used_at__isnull = True
        )
    
    def create_new(self, **kwargs):
        """creates a new reply address"""
        kwargs['allowed_from_email'] = kwargs['user'].email
        reply_address = ReplyAddress(**kwargs)
        while True:
            reply_address.address = ''.join(random.choice(string.letters +
                string.digits) for i in xrange(random.randint(12, 25))).lower()
            if self.filter(address = reply_address.address).count() == 0:
                break
        reply_address.save()
        return reply_address
			

REPLY_ACTION_CHOICES = (
    ('post_answer', _('Post an answer')),
    ('post_comment', _('Post a comment')),
    ('auto_answer_or_comment', _('Answer or comment, depending on the size of post')),
    ('validate_email', _('Validate email and record signature')),
)
class ReplyAddress(models.Model):
    """Stores a reply address for the post
    and the user"""
    address = models.CharField(max_length = 25, unique = True)
    post = models.ForeignKey(
                            Post,
                            null = True,#reply not necessarily to posts
                            related_name = 'reply_addresses'
                        )#the emailed post
    reply_action = models.CharField(
                        max_length = 32,
                        choices = REPLY_ACTION_CHOICES,
                        default = 'auto_answer_or_comment'
                    )
    response_post = models.ForeignKey(
                            Post,
                            null = True,
                            related_name = 'edit_addresses'
                        )
    user = models.ForeignKey(User)
    allowed_from_email = models.EmailField(max_length = 150)
    used_at = models.DateTimeField(null = True, default = None)

    objects = ReplyAddressManager()


    class Meta:
        app_label = 'askbot'
        db_table = 'askbot_replyaddress'

    @property
    def was_used(self):
        """True if was used"""
        return self.used_at != None

    def edit_post(self, parts):
        """edits the created post upon repeated response
        to the same address"""
        assert self.was_used == True
        content, stored_files = mail.process_parts(parts)
        self.user.edit_post(
            post = self.response_post,
            body_text = content,
            revision_comment = _('edited by email'),
            by_email = True
        )
        self.response_post.thread.invalidate_cached_data()

    def create_reply(self, parts):
        """creates a reply to the post which was emailed
        to the user
        """
        result = None
        #todo: delete stored files if this function fails
        content, stored_files = mail.process_parts(parts)

        if self.post.post_type == 'answer':
            result = self.user.post_comment(
                                        self.post,
                                        content,
                                        by_email = True
                                    )
        elif self.post.post_type == 'question':
            if self.reply_action == 'auto_answer_or_comment':
                wordcount = len(content)/6#todo: this is a simplistic hack
                if wordcount > askbot_settings.MIN_WORDS_FOR_ANSWER_BY_EMAIL:
                    reply_action = 'post_answer'
                else:
                    reply_action = 'post_comment'
            else:
                reply_action = self.reply_action

            if reply_action == 'post_answer':
                result = self.user.post_answer(
                                            self.post,
                                            content,
                                            by_email = True
                                        )
            elif reply_action == 'post_comment':
                result = self.user.post_comment(
                                            self.post,
                                            content,
                                            by_email = True
                                        )
            else:
                logging.critical(
                    'Unexpected reply action: "%s", post by email failed' % reply_action
                )
                return None#todo: there may be a better action to take here...
        elif self.post.post_type == 'comment':
            result = self.user.post_comment(
                                    self.post.parent,
                                    content,
                                    by_email = True
                                )
        result.thread.invalidate_cached_data()
        self.response_post = result
        self.used_at = datetime.now()
        self.save()
        return result