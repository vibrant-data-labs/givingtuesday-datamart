import vdl_tools.py2mappr as mappr
import vdl_tools.py2mappr.publish as publish
import vdl_tools.py2mappr.vdl_palette as pal
import pandas as pd
from vdl_tools.shared_tools.tools.logger import logger


def get_attribute_settings(attrib_settings_path):
    # read player attribute settings
    df_attrib_settings = pd.read_excel(attrib_settings_path)
    df_attrib_settings = df_attrib_settings[df_attrib_settings['Keep'] == 1]
    # for each settings column create a list of attributes that = 1 and name the list by the column name.
    # This is the format that the player expects for the player attribute settings file
    settings_list = [col for col in df_attrib_settings.columns.tolist() if col not in ['Name', 'Display_Name']]
    attrib_settings = {}  # dict to hold the setting name and associated list of attributes
    for setting in settings_list:
        # get list of attributes that = 1 for each setting
        attrib_settings[setting] = list(df_attrib_settings[df_attrib_settings[setting] == 1].Display_Name)
    # create a dictionary of attribute descriptions just for the attributes that have a tooltip
    df_tooltips = df_attrib_settings[df_attrib_settings.tooltip.notnull()]
    attr_descriptions = dict(zip(df_tooltips.Display_Name, df_tooltips.tooltip))
    return attrib_settings, attr_descriptions


def build_player(
    ndf,
    playerpath,
    ldf=None,
    player_bucket=None,
    launch_local=True,
    upload_s3=False,
    clus_snap_desr="",
    geo_snap_descr="",
    proj_descr="",
    proj_title="Default Project Title",
    node_color="Cluster Theme",
    node_size="Funding Total",
    draw_edges=False,  # draw links overall
    node_size_scaling=(7, 14, .7),
    sponsors=None,  # list of sponsor tuples [('Name', 'logo_url', 'org_url')]
    attrib_settings=None,  # dictionary of attribute settings
    attrib_tooltips=None,  # dictionary of attribute tooltips
):

    image_show = False
    link_curve = 0
    link_weight = .4
    neighbors = 1
    node_labels = True

    #######################################################################

    project, snap_clus = mappr.create_map(ndf, network_df=ldf)

    #######################################################################
    # clustered snapshot : climate Embedding themes

    snap_clus.set_nodes(
        node_color=node_color,
        node_size=node_size,
        node_size_scaling=node_size_scaling,  # min size, max size, multiplier
    )

    snap_clus.set_palette(pal.cat_palette, pal.num_palette)

    snap_clus.settings.update({
        "nodeImageShow": image_show,
        "drawLabels": node_labels,
        "drawEdges": draw_edges,
        # "nodeSizeScaleStrategy": "linear",  # "log",  # "linear"
    })

    if draw_edges:
        logger.info('setting links')
        snap_clus.set_links(
            link_curve=link_curve,
            link_weight=link_weight,
            neighbors=neighbors,
        )

    snap_clus.set_clusters()

    snap_clus.set_display_data(
        title="Cluster Themes",
        subtitle="X25 Companies self-organized into emergent themes based on shared language.",
        description=clus_snap_desr
    )


    #######################################################################
    # geo snapshot

    snap_geo = mappr.create_layout(layout_type="geo")
    snap_geo.set_nodes(
        node_color=node_color,
        node_size=node_size,
        node_size_scaling=(7, 15, .7),  # min size, max size, multiplier
    )
    snap_geo.set_palette(pal.cat_palette, pal.num_palette)

    snap_geo.settings.update({
        "nodeImageShow": image_show,
        "drawLabels": node_labels,
        "drawEdges": draw_edges,
    })

    snap_geo.set_geo_config(
        min_level='nodes',
        max_level='countries',
        default_level='nodes'
    )

    if draw_edges:
        snap_geo.set_links(
            link_curve=link_curve,
            link_weight=link_weight,
            neighbors=neighbors,
        )

    snap_geo.set_display_data(
        title="Geographic View",
        subtitle="Company and Organization headquarters",
        description=geo_snap_descr
    )

    # add project title and description
    project.set_display_data(
        title=proj_title,
        description=proj_descr,
        sponsors_txt="Partners:"
    )

    project.configuration.update({
        "startPage": "legend",
        "showStartInfo": True,
        "defaultPanel": "Map Information",
        "displayExportButton": False
    })

    project.set_feedback({
        "type": "email",
        "link": "info@vibrantdatalabs.org",
        "text": "questions?",
    })
    # project.set_beta()

    if sponsors != None:
        project.create_sponsor_list(sponsors)

    # project.publish_settings.update({
    #     "gtag_id": "G-2WY2EWGTJM"
    # })

    project.snapshots = [
        snap_clus,
        snap_geo,
    ]

    project.update_attributes(
        visible_filters=attrib_settings['visible_filters'],
        visible_profile=attrib_settings['visible_profile'],
        visible_search=attrib_settings['visible_search'],
        text_str=attrib_settings['free_text'],
        list_string=attrib_settings['tag_list'],
        tag_cloud=attrib_settings['tags_4'],
        tags_3=attrib_settings['tags_3'],
        tags_2=attrib_settings['tags_2'],
        wide_tags=attrib_settings['tags_1'],
        horizontal_bars=attrib_settings['horizontal_bars'],
        years=attrib_settings['years'],
        low_priority=attrib_settings['low_priority'],
        color_select=attrib_settings['color_select'],
        size_select=attrib_settings['size_select'],
        axis_select=attrib_settings['axis_select'],
        urls=attrib_settings['urls'],
        attr_descriptions=attrib_tooltips,
    )

    workers = []
    if upload_s3:
        workers.append(publish.s3(player_bucket))

    if launch_local:
        workers.append(publish.local())

    publish.run(workers, playerpath)
