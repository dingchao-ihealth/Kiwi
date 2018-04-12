# -*- coding: utf-8 -*-
# pylint: disable=invalid-name

import datetime
from mock import patch

from django.urls import reverse
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings

from tcms import signals
from .models import UserActivateKey


# ### Test cases for models ###


class TestSetRandomKey(TestCase):
    """Test case for UserActivateKey.set_random_key_for_user"""

    @classmethod
    def setUpTestData(cls):
        cls.new_user = User.objects.create(  # nosec:B106:hardcoded_password_funcarg
            username='new-tester',
            email='new-tester@example.com',
            password='password')

    @patch('tcms.core.contrib.auth.models.datetime')
    def test_set_random_key(self, mock_datetime):
        now = datetime.datetime.now()
        in_7_days = datetime.timedelta(7)

        mock_datetime.datetime.today.return_value = now
        mock_datetime.timedelta.return_value = in_7_days

        activation_key = UserActivateKey.set_random_key_for_user(self.new_user)
        self.assertEqual(self.new_user, activation_key.user)
        self.assertNotEqual('', activation_key.activation_key)
        self.assertEqual(now + in_7_days, activation_key.key_expires)


class TestForceToSetRandomKey(TestCase):
    """Test case for UserActivateKey.set_random_key_for_user forcely"""

    @classmethod
    def setUpTestData(cls):
        cls.new_user = User.objects.create(  # nosec:B106:hardcoded_password_funcarg
            username='new-tester',
            email='new-tester@example.com',
            password='password')
        cls.origin_activation_key = UserActivateKey.set_random_key_for_user(cls.new_user)

    def test_set_random_key_forcely(self):
        new_activation_key = UserActivateKey.set_random_key_for_user(self.new_user,
                                                                     force=True)
        self.assertEqual(self.origin_activation_key.user, new_activation_key.user)
        self.assertNotEqual(self.origin_activation_key.activation_key,
                            new_activation_key.activation_key)


# ### Test cases for view methods ###

class TestLogout(TestCase):
    """Test for logout view method"""

    @classmethod
    def setUpTestData(cls):
        super(TestLogout, cls).setUpTestData()

        cls.tester = User.objects.create_user(  # nosec:B106:hardcoded_password_funcarg
            username='authtester',
            email='authtester@example.com',
            password='password')
        cls.logout_url = reverse('tcms-logout')

    def test_logout_then_redirect_to_next(self):
        self.client.login(  # nosec:B106:hardcoded_password_funcarg
            username=self.tester.username,
            password='password')
        response = self.client.get(self.logout_url, follow=True)
        self.assertRedirects(response, reverse('tcms-login'))

    def test_logout_then_goto_next(self):
        self.client.login(  # nosec:B106:hardcoded_password_funcarg
            username=self.tester.username,
            password='password')
        next_url = reverse('plans-all')
        response = self.client.get(self.logout_url, {'next': next_url}, follow=True)
        self.assertRedirects(response, next_url)


class TestRegistration(TestCase):

    def setUp(self):
        self.register_url = reverse('tcms-register')
        self.fake_activate_key = 'secret-activate-key'

    def test_open_registration_page(self):
        response = self.client.get(self.register_url)
        self.assertContains(
            response,
            '<input value="Register" class="loginbutton sprites" type="submit">',
            html=True)

    def assert_user_registration(self, username, follow=False):

        with patch('tcms.core.contrib.auth.models.secrets') as _secrets:
            _secrets.token_hex.return_value = self.fake_activate_key

            response = self.client.post(self.register_url,
                                        {'username': username,
                                         'password1': 'password',
                                         'password2': 'password',
                                         'email': 'new-tester@example.com'},
                                        follow=follow)

        user = User.objects.get(username=username)
        self.assertEqual('new-tester@example.com', user.email)
        self.assertFalse(user.is_active)

        key = UserActivateKey.objects.get(user=user)
        self.assertEqual(self.fake_activate_key, key.activation_key)

        return response

    @patch('tcms.signals.USER_REGISTERED_SIGNAL.send')
    def test_register_user_sends_signal(self, signal_mock):
        self.assert_user_registration('new-signal-tester')
        self.assertTrue(signal_mock.called)
        self.assertEqual(1, signal_mock.call_count)

    @override_settings(ADMINS=[('Test Admin', 'admin@kiwitcms.org')])
    @patch('tcms.core.utils.mailto.send_mail')
    def test_signal_handler_notifies_admins(self, send_mail):
        # connect the handler b/c it is not connected by default
        signals.USER_REGISTERED_SIGNAL.connect(signals.notify_admins)

        try:
            response = self.assert_user_registration('signal-handler')
            self.assertRedirects(response, reverse('core-views-index'), target_status_code=302)

            # 1 - verification mail, 2 - email to admin
            self.assertTrue(send_mail.called)
            self.assertEqual(2, send_mail.call_count)

            # verify we've actually sent the admin email
            self.assertIn('New user awaiting approval', send_mail.call_args_list[0][0][0])
            self.assertIn('somebody just registered an account with username signal-handler',
                          send_mail.call_args_list[0][0][1])
            self.assertIn('admin@kiwitcms.org', send_mail.call_args_list[0][0][-1])
        finally:
            signals.USER_REGISTERED_SIGNAL.disconnect(signals.notify_admins)

    @patch('tcms.core.utils.mailto.send_mail')
    def test_register_user_by_email_confirmation(self, send_mail):
        response = self.assert_user_registration('new-tester', follow=True)
        self.assertContains(
            response,
            'Your account has been created, please check your mailbox for confirmation'
        )

        site = Site.objects.get_current()
        confirm_url = 'http://%s%s' % (site.domain, reverse('tcms-confirm',
                                                            args=[self.fake_activate_key]))

        # Verify notification mail
        send_mail.assert_called_once_with(
            settings.EMAIL_SUBJECT_PREFIX + 'Your new 127.0.0.1:8000 account confirmation',
            """Welcome, new-tester, and thanks for signing up for an 127.0.0.1:8000 account!


%s
""" % confirm_url,
            settings.DEFAULT_FROM_EMAIL, ['new-tester@example.com'], fail_silently=False)

    @override_settings(AUTO_APPROVE_NEW_USERS=False,
                       ADMINS=[('admin1', 'admin1@example.com'),
                               ('admin2', 'admin2@example.com')])
    def test_register_user_and_activate_by_admin(self):
        response = self.assert_user_registration('plan-tester', follow=True)

        self.assertContains(
            response,
            'Your account has been created, but you need an administrator to activate it')

        for (name, email) in settings.ADMINS:
            self.assertContains(response,
                                '<a href="mailto:{}">{}</a>'.format(email, name),
                                html=True)


class TestConfirm(TestCase):
    """Test for activation key confirmation"""

    @classmethod
    def setUpTestData(cls):
        cls.new_user = User.objects.create(  # nosec:B106:hardcoded_password_funcarg
            username='new-user',
            email='new-user@example.com',
            password='password')

    def setUp(self):
        self.new_user.is_active = False
        self.new_user.save()

    def test_fail_if_activation_key_does_not_exist(self):
        confirm_url = reverse('tcms-confirm',
                              args=['nonexisting-activation-key'])
        response = self.client.get(confirm_url, follow=True)

        self.assertContains(
            response,
            'This activation key no longer exists in the database')

        # user account not activated
        user = User.objects.get(username=self.new_user.username)
        self.assertFalse(user.is_active)

    def test_fail_if_activation_key_expired(self):
        fake_activation_key = 'secret-activation-key'

        with patch('tcms.core.contrib.auth.models.secrets') as _secrets:
            _secrets.token_hex.return_value = fake_activation_key
            key = UserActivateKey.set_random_key_for_user(self.new_user)
            key.key_expires = datetime.datetime.now() - datetime.timedelta(days=10)
            key.save()

        confirm_url = reverse('tcms-confirm', args=[fake_activation_key])
        response = self.client.get(confirm_url, follow=True)

        self.assertContains(response, 'This activation key has expired')

        # user account not activated
        user = User.objects.get(username=self.new_user.username)
        self.assertFalse(user.is_active)

    def test_confirm(self):
        fake_activate_key = 'secret-activate-key'

        with patch('tcms.core.contrib.auth.models.secrets') as _secrets:
            _secrets.token_hex.return_value = fake_activate_key
            UserActivateKey.set_random_key_for_user(self.new_user)

        confirm_url = reverse('tcms-confirm',
                              args=[fake_activate_key])
        response = self.client.get(confirm_url, follow=True)

        self.assertContains(
            response,
            'Your account has been activated successfully')

        # user account activated
        user = User.objects.get(username=self.new_user.username)
        self.assertTrue(user.is_active)
        activate_key_deleted = not UserActivateKey.objects.filter(user=user).exists()
        self.assertTrue(activate_key_deleted)
