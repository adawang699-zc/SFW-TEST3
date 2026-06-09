from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0011_make_porttestresult_mapping_nullable'),
    ]

    operations = [
        migrations.CreateModel(
            name='LinkAddress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='名称')),
                ('url', models.URLField(max_length=500, verbose_name='地址')),
                ('order', models.IntegerField(default=0, verbose_name='排序')),
            ],
            options={
                'verbose_name': '关联地址',
                'verbose_name_plural': '关联地址',
                'ordering': ['order', 'id'],
            },
        ),
    ]
