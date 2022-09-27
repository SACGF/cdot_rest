"""
The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
"""
from django.urls import path

from cdot_rest import views

urlpatterns = [
    path('', views.index, name='index'),
    path('transcript/<transcript_version>', views.transcript, name='transcript'),
    path('gene/<gene_symbol>', views.gene, name='gene'),
    path('transcripts/gene/<gene_symbol>', views.transcripts_for_gene,
         name='transcripts_for_gene'),
    path('transcripts/region/<contig>/<aln_method>/<int:start>/<int:end>', views.transcripts_for_region,
         name='transcripts_for_region'),
]
