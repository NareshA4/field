from django.urls import path

from . import api_views

urlpatterns = [
    path("workspace/jobs/", api_views.MyJobsAPIView.as_view(), name="api_my_jobs"),
    path("workspace/jobs/<uuid:pk>/", api_views.JobDetailAPIView.as_view(), name="api_job_detail"),
    path("workspace/jobs/<uuid:pk>/drawer/", api_views.JobExecutionDrawerAPIView.as_view(), name="api_job_drawer"),
    path("workspace/jobs/<uuid:pk>/start/", api_views.JobStartAPIView.as_view(), name="api_job_start"),
    path("workspace/jobs/<uuid:pk>/pause/", api_views.JobPauseAPIView.as_view(), name="api_job_pause"),
    path("workspace/jobs/<uuid:pk>/resume/", api_views.JobResumeAPIView.as_view(), name="api_job_resume"),
    path("workspace/jobs/<uuid:pk>/travel/", api_views.JobTravelLogAPIView.as_view(), name="api_job_travel"),
    path("workspace/jobs/<uuid:pk>/complete/", api_views.JobCompleteAPIView.as_view(), name="api_job_complete"),
    path("workspace/jobs/<uuid:pk>/notes/", api_views.JobWorkNoteAPIView.as_view(), name="api_job_notes"),
    path("workspace/jobs/<uuid:pk>/labor/", api_views.JobLaborAPIView.as_view(), name="api_job_labor"),
    path("workspace/jobs/<uuid:pk>/materials/", api_views.JobMaterialAPIView.as_view(), name="api_job_materials"),
    path("workspace/jobs/<uuid:pk>/parts/consume/", api_views.JobPartConsumeAPIView.as_view(), name="api_job_parts_consume"),
    path("workspace/jobs/<uuid:pk>/parts/return/", api_views.JobPartReturnAPIView.as_view(), name="api_job_parts_return"),
    path("workspace/jobs/<uuid:pk>/evidence/", api_views.JobEvidenceAPIView.as_view(), name="api_job_evidence"),
    path("workspace/jobs/<uuid:pk>/meters/reading/", api_views.JobMeterReadingAPIView.as_view(), name="api_job_meter_reading"),
    path("workspace/jobs/<uuid:pk>/verify-asset/", api_views.JobAssetVerifyAPIView.as_view(), name="api_job_verify_asset"),
    path("workspace/jobs/<uuid:pk>/defects/", api_views.JobReportDefectAPIView.as_view(), name="api_job_report_defect"),
    path("workspace/capacity/", api_views.TechnicianCapacityAPIView.as_view(), name="api_technician_capacity"),
]
