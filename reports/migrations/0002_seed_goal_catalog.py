from django.db import migrations

CATALOG = {
    'A': [('Enter Employment', True), ('Retain Employment', True), ('Leave public assistance', False)],
    'B': [('Achieve work-based project learner goal', False), ('Enter Occupational Skills Training Program', True), ('Enter Postsecondary Education', True), ('Obtain High School Diploma', True)],
    'C': [('Help more frequently with school', False), ("Increase contact with child(ren)'s teachers", False), ("More involvement in child(ren)'s school activities", False), ('Purchase books or magazines', False), ('Read to child(ren)', False), ('Visit the library (with/for child(ren))', False)],
    'D': [('Obtain citizenship', True), ('Achieve civics skills', False), ('Increase involvement in community activities', False), ('Vote or register to vote', False)],
}


def seed_catalog(apps, schema_editor):
    Goal = apps.get_model('reports', 'Goal')
    for category, goals in CATALOG.items():
        for order, (text, reportable) in enumerate(goals, 1):
            Goal.objects.using(schema_editor.connection.alias).create(category=category, text=text, is_reportable=reportable, order=order)


def remove_catalog(apps, schema_editor):
    Goal = apps.get_model('reports', 'Goal')
    for category, goals in CATALOG.items():
        Goal.objects.using(schema_editor.connection.alias).filter(category=category, text__in=[text for text, _ in goals]).delete()


class Migration(migrations.Migration):
    dependencies = [('reports', '0001_initial')]
    operations = [migrations.RunPython(seed_catalog, remove_catalog)]
