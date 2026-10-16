from django.urls import path

from . import views

app_name = "workspace"

urlpatterns = [
    path("workspace/", views.MyJobsView.as_view(), name="my_jobs"),
    path("workspace/my-jobs/", views.MyJobsView.as_view()),
    path("workspace/jobs/<uuid:pk>/", views.JobDetailView.as_view(), name="job_detail"),
    path("workspace/jobs/<uuid:pk>/drawer/", views.JobExecutionDrawerView.as_view(), name="drawer"),
    path("workspace/jobs/<uuid:pk>/start/", views.JobStartView.as_view(), name="start"),
    path("workspace/jobs/<uuid:pk>/pause/", views.JobPauseView.as_view(), name="pause"),
    path("workspace/jobs/<uuid:pk>/resume/", views.JobResumeView.as_view(), name="resume"),
    path("workspace/jobs/<uuid:pk>/travel/", views.JobTravelLogView.as_view(), name="travel"),
    path("workspace/jobs/<uuid:pk>/complete/", views.JobCompleteView.as_view(), name="complete"),
    path("workspace/jobs/<uuid:pk>/notes/", views.JobWorkNoteView.as_view(), name="notes"),
    path("workspace/jobs/<uuid:pk>/labor/", views.JobLaborView.as_view(), name="labor"),
    path("workspace/jobs/<uuid:pk>/materials/", views.JobMaterialView.as_view(), name="material"),
    path("workspace/jobs/<uuid:pk>/parts/consume/", views.JobPartConsumeView.as_view(), name="consume_part"),
    path("workspace/jobs/<uuid:pk>/evidence/", views.JobEvidenceUploadView.as_view(), name="evidence"),
    # Aliases without workspace/ prefix
    path("my-jobs/", views.MyJobsView.as_view()),
    path("jobs/<uuid:pk>/", views.JobDetailView.as_view()),
    path("workspace/<uuid:pk>/", views.JobDetailView.as_view()),
    path("jobs/<uuid:pk>/drawer/", views.JobExecutionDrawerView.as_view()),
    path("jobs/<uuid:pk>/start/", views.JobStartView.as_view()),
    path("jobs/<uuid:pk>/pause/", views.JobPauseView.as_view()),
    path("jobs/<uuid:pk>/resume/", views.JobResumeView.as_view()),
    path("jobs/<uuid:pk>/travel/", views.JobTravelLogView.as_view()),
    path("jobs/<uuid:pk>/complete/", views.JobCompleteView.as_view()),
    path("jobs/<uuid:pk>/notes/", views.JobWorkNoteView.as_view()),
    path("jobs/<uuid:pk>/labor/", views.JobLaborView.as_view()),
    path("jobs/<uuid:pk>/materials/", views.JobMaterialView.as_view()),
    path("jobs/<uuid:pk>/parts/consume/", views.JobPartConsumeView.as_view()),
    path("jobs/<uuid:pk>/evidence/", views.JobEvidenceUploadView.as_view()),
]
