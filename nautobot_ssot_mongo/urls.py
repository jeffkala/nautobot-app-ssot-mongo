"""Django urlpatterns declaration for nautobot_ssot_mongo app."""

from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView
from nautobot.apps.urls import NautobotUIViewSetRouter

from nautobot_ssot_mongo import views

app_name = "nautobot_ssot_mongo"
router = NautobotUIViewSetRouter()
router.register("federated-instances", views.FederatedInstanceUIViewSet)
router.register("ownership-rules", views.OwnershipRuleUIViewSet)

urlpatterns = [
    path("docs/", RedirectView.as_view(url=static("nautobot_ssot_mongo/docs/index.html")), name="docs"),
]

urlpatterns += router.urls
