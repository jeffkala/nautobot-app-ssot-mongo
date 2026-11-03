"""Django API urlpatterns declaration for nautobot_ssot_mongo app."""

from nautobot.apps.api import OrderedDefaultRouter

from nautobot_ssot_mongo.api import views

router = OrderedDefaultRouter()
router.register("ownership-rules", views.OwnershipRuleViewSet)

app_name = "nautobot_ssot_mongo-api"
urlpatterns = router.urls
