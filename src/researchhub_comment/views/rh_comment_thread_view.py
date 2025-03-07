from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.viewsets import ModelViewSet

from researchhub_comment.serializers.rh_comment_thread_serializer import (
    RhCommentThreadSerializer,
)
from researchhub_comment.models import RhCommentModel, RhCommentThreadModel
from researchhub_comment.views.filters.rh_comment_thread_filters import (
    RhCommentThreadFilter,
)
from researchhub_comment.views.rh_comment_thread_view_mixin import (
    RhCommentThreadViewMixin,
)


class RhCommentThreadViewSet(RhCommentThreadViewMixin, ModelViewSet):
    filter_backends = (DjangoFilterBackend,)
    filter_class = RhCommentThreadFilter
    permission_classes = [
        # IsAuthenticatedOrReadOnly,
        AllowAny,  # TODO: calvinhlee replace with above permissions
    ]
    ordering = "-updated_date"
    queryset = RhCommentThreadModel.objects.filter()
    serializer_class = RhCommentThreadSerializer

    def create(self, request, *args, **kwargs):
        return Response(
            "Directly creating RhCommentThread with view is prohibited. Use /create_comment",
            status=status.HTTP_400_BAD_REQUEST,
        )

    def get_filtered_queryset(self):
        return self.filter_queryset(self.get_queryset())
