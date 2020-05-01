from allauth.socialaccount.helpers import render_authentication_error
from allauth.socialaccount.models import SocialLogin, SocialAccount
from allauth.socialaccount.providers.base import ProviderException
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.orcid.provider import OrcidProvider
from allauth.socialaccount.providers.orcid.views import OrcidOAuth2Adapter

from allauth.socialaccount.providers.oauth2.client import (
    OAuth2Error,
    OAuth2Client
)
from allauth.socialaccount.providers.oauth2.views import (
    AuthError,
    OAuth2CallbackView,
    PermissionDenied,
    RequestException
)
from allauth.utils import get_request_param, get_user_model
from allauth.account.signals import user_signed_up, user_logged_in
from allauth.account import app_settings

from rest_auth.registration.views import SocialLoginView
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from django.dispatch import receiver
from django.http import HttpRequest
from django.utils.translation import ugettext_lazy as _

from oauth.helpers import complete_social_login
from oauth.exceptions import LoginError
from oauth.utils import get_orcid_names
from researchhub.settings import GOOGLE_REDIRECT_URL
from user.models import Author
from user.utils import merge_author_profiles
from utils import sentry
from utils.http import http_request, RequestMethods
from analytics.models import WebsiteVisits


class SocialLoginSerializer(serializers.Serializer):
    access_token = serializers.CharField(required=False, allow_blank=True)
    code = serializers.CharField(required=False, allow_blank=True)
    uuid = serializers.CharField(required=False, allow_blank=True)

    def _get_request(self):
        request = self.context.get('request')
        if not isinstance(request, HttpRequest):
            request = request._request
        return request

    def get_social_login(self, adapter, app, token, response):
        """
        :param adapter: allauth.socialaccount Adapter subclass.
            Usually OAuthAdapter or Auth2Adapter
        :param app: `allauth.socialaccount.SocialApp` instance
        :param token: `allauth.socialaccount.SocialToken` instance
        :param response: Provider's response for OAuth1. Not used in the
        :returns: A populated instance of the
            `allauth.socialaccount.SocialLoginView` instance
        """
        request = self._get_request()
        social_login = adapter.complete_login(
            request,
            app,
            token,
            response=response
        )
        social_login.token = token
        return social_login

    def validate(self, attrs):
        view = self.context.get('view')
        request = self._get_request()

        if not view:
            raise serializers.ValidationError(
                _("View is not defined, pass it as a context variable")
            )

        adapter_class = getattr(view, 'adapter_class', None)
        if not adapter_class:
            raise serializers.ValidationError(
                _("Define adapter_class in view")
            )

        adapter = adapter_class(request)
        app = adapter.get_provider().get_app(request)

        # More info on code vs access_token
        # http://stackoverflow.com/questions/8666316/facebook-oauth-2-0-code-and-token

        # Case 1: We received the access_token
        if attrs.get('access_token'):
            access_token = attrs.get('access_token')

        # Case 2: We received the authorization code
        elif attrs.get('code'):
            self.callback_url = getattr(view, 'callback_url', None)
            self.client_class = getattr(view, 'client_class', None)

            if not self.callback_url:
                error = serializers.ValidationError(
                    _("Define callback_url in view")
                )
                sentry.log_error(error)
                raise error
            if not self.client_class:
                error = serializers.ValidationError(
                    _("Define client_class in view")
                )
                sentry.log_error(error)
                raise error

            code = attrs.get('code')

            provider = adapter.get_provider()
            scope = provider.get_scope(request)
            client = self.client_class(
                request,
                app.client_id,
                app.secret,
                adapter.access_token_method,
                adapter.access_token_url,
                self.callback_url,
                scope
            )
            token = client.get_access_token(code)
            access_token = token['access_token']

        else:
            error = serializers.ValidationError(
                _("Incorrect input. access_token or code is required."))
            sentry.log_error(error)
            raise serializers.ValidationError(
                _("Incorrect input. access_token or code is required."))

        social_token = adapter.parse_token({'access_token': access_token})
        social_token.app = app

        try:
            login = self.get_social_login(
                adapter,
                app,
                social_token,
                access_token
            )
            complete_social_login(request, login)
        except Exception as e:
            error = LoginError(e, 'Login failed')
            sentry.log_error(error, base_error=e)
            raise serializers.ValidationError(_("Incorrect value"))

        if not login.is_existing:
            # We have an account already signed up in a different flow
            # with the same email address: raise an exception.
            # This needs to be handled in the frontend. We can not just
            # link up the accounts due to security constraints
            if app_settings.UNIQUE_EMAIL:
                # Do we have an account already with this email address?
                account_exists = get_user_model().objects.filter(
                    email=login.user.email,
                ).exists()
                if account_exists:
                    sentry.log_info('User already registered with this e-mail')
                    raise serializers.ValidationError(
                        _("User already registered with this e-mail address.")
                    )

            login.lookup()
            login.save(request, connect=True)

        attrs['user'] = login.account.user
        try:
            visits = WebsiteVisits.objects.get(uuid=attrs['uuid'])
            visits.user = attrs['user']
            visits.save()
        except Exception as e:
            print(e)
            sentry.log_error(e)
            pass

        return attrs


class GoogleLogin(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    callback_url = GOOGLE_REDIRECT_URL
    client_class = OAuth2Client
    serializer_class = SocialLoginSerializer


class CallbackView(OAuth2CallbackView):
    """
    This class is copied from allauth/socialaccount/providers/oauth2/views.py
    but uses a custom method for `complete_social_login`
    """
    permission_classes = (AllowAny,)

    def dispatch(self, request, *args, **kwargs):
        if 'error' in request.GET or 'code' not in request.GET:
            # Distinguish cancel from error
            auth_error = request.GET.get('error', None)
            if auth_error == self.adapter.login_cancelled_error:
                error = AuthError.CANCELLED
            else:
                error = AuthError.UNKNOWN
            return render_authentication_error(
                request,
                self.adapter.provider_id,
                error=error)
        app = self.adapter.get_provider().get_app(self.request)
        client = self.get_client(request, app)
        try:
            access_token = client.get_access_token(request.GET['code'])
            token = self.adapter.parse_token(access_token)
            token.app = app
            login = self.adapter.complete_login(request,
                                                app,
                                                token,
                                                response=access_token)
            login.token = token
            if self.adapter.provider_id != OrcidProvider.id:
                if self.adapter.supports_state:
                    login.state = SocialLogin \
                        .verify_and_unstash_state(
                            request,
                            get_request_param(request, 'state'))
                else:
                    login.state = SocialLogin.unstash_state(request)
            return complete_social_login(request, login)
        except (PermissionDenied,
                OAuth2Error,
                RequestException,
                ProviderException) as e:
            return render_authentication_error(
                request,
                self.adapter.provider_id,
                exception=e)


google_callback = CallbackView.adapter_view(GoogleOAuth2Adapter)
orcid_callback = CallbackView.adapter_view(OrcidOAuth2Adapter)


@api_view([RequestMethods.POST])
@permission_classes([IsAuthenticated])
def orcid_connect(request):
    success = False
    status = 400

    try:
        orcid = request.data.get('orcid')
        access_token = request.data.get('access_token')
        url = f'https://pub.orcid.org/v3.0/{orcid}/record'
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        # Raise for status because we need to make sure we can authenticate
        # correctly with orcid. Without this check, anyone could make a post
        # request to connect any other orcid account to their own.
        response = http_request(RequestMethods.GET, url=url, headers=headers)
        response.raise_for_status()
        user = request.user

        save_orcid_author(user, orcid, response.json())

        success = True
        status = 201
        data = {
            'success': success,
            'orcid_profile': f'https://orcid.org/{orcid}'
        }
    except Exception as e:
        data = str(e)

    return Response(data, status=status)


def save_orcid_author(user, orcid_id, orcid_data):
    orcid_account = SocialAccount.objects.create(
        user=user,
        uid=orcid_id,
        provider=OrcidProvider.id,
        extra_data=orcid_data
    )
    update_author_profile(user, orcid_id, orcid_data, orcid_account)


def update_author_profile(user, orcid_id, orcid_data, orcid_account):
    first_name, last_name = get_orcid_names(orcid_data)

    try:
        author = Author.objects.get(orcid_id=orcid_id)
    except Author.DoesNotExist:
        user.author_profile.orcid_id = orcid_id
    else:
        user.author_profile = merge_author_profiles(
            user.author_profile,
            author
        )

    user.author_profile.orcid_account = orcid_account
    user.author_profile.first_name = first_name
    user.author_profile.last_name = last_name
    user.author_profile.save()
    user.save()


@receiver(user_signed_up)
@receiver(user_logged_in)
def user_signed_up_(request, user, **kwargs):
    """After a user signs up with social account, set their profile image"""

    queryset = SocialAccount.objects.filter(
        provider='google',
        user=user
    )

    if queryset.exists():
        if queryset.count() > 1:
            raise Exception(
                f'Expected 1 item in the queryset. Found {queryset.count()}.'
            )

        google_account = queryset.first()
        url = google_account.extra_data.get('picture', None)

        if user.author_profile and not user.author_profile.profile_image:
            user.author_profile.profile_image = url
            user.author_profile.save()
        return None

    else:
        return None
