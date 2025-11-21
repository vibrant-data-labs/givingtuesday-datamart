import pandas as pd

import vdl_tools.network_tools.network_functions as net
import vdl_tools.shared_tools.common_functions as cf
import vdl_tools.shared_tools.project_config as pc
from vdl_tools.shared_tools.tools.logger import logger

from vdl_project_template.player_building import build_network as bcn
from vdl_project_template.player_building import decorate_player as dfp
from vdl_project_template.player_building import build_player as bcp


BUILD_PLAYER = True
BUILD_NETWORK = False


# SETTINGS
paths = pc.get_paths()

player_path = paths['player_path']
player_attrib_settings_path = paths['player_attribute_settings']
bucket = 'vdl-project-template'

# orgs with all original metdata
infile = paths['enriched_data_path']
# final network files
nw_name = paths['nw_name']
nw_name_cleaned = paths['nw_name_cleaned']
# final player files
player_dir = paths['player_dir']


cf.create_folders(nw_name)
cf.create_folders(nw_name_cleaned)

# LOAD AND PREP DATA
print('loading data')
df = pd.read_json(infile)
print(f'Loaded: {len(df)} items')

if bucket == 'vdl-project-template':
    raise ValueError(
        'Bucket is set to vdl-project-template. Please set the bucket to a valid production bucket.'
    )


if BUILD_NETWORK:
    # NETWORK PARAMETERS
    linksPer = 4
    min_clus_size_optimize = 5
    min_clus_size_final = 3

    logger.info(f"BUILDING NETWORK for: {len(df)} organizations with at least {1} keywords")

    if 'id' in df.columns:
        logger.warning('id column already exists in df_trimmed, dropping it')
        df.pop('id')

    logger.info('building embedding network')
    embedding_ndf, embedding_ldf = bcn.build_decorate_network(
        df,
        linksPer=linksPer,  # links per node
        nw_name=nw_name,  # final filename for network,
        textcol='Summary',
        cluster_naming_subject='banking',
        id_col='uuid',
    )

    # write network to json
    logger.info("\nWriting basic decorated network file to json")
    net.write_network_to_json(embedding_ndf, embedding_ldf, nw_name)

    ndf = embedding_ndf
    ldf = embedding_ldf

else:
    logger.info('\nLoading decorated network file\n')
    ndf, ldf = net.open_network_from_json(nw_name)


# fill empty lat/long with center of the US
ndf['Latitude'] = ndf['Latitude'].fillna(39.8283)
ndf['Longitude'] = ndf['Longitude'].fillna(-98.5795)

ndf['Latitude'] = ndf['Latitude'].apply(lambda x: x if x else 39.8283)
ndf['Longitude'] = ndf['Longitude'].apply(lambda x: x if x else -98.5795)

ndf = dfp.rename_clean_for_player(ndf, player_attrib_settings_path)

# %% BUILD PLAYER #
if BUILD_PLAYER:
    net.write_network_to_json(ndf, ldf, nw_name_cleaned)
    # also write to nodes to excel for inspection

    # build player
    player_dir.mkdir(parents=True, exist_ok=True)
    # get dictionary of attribute settings
    attrib_settings, attrib_tooltips = bcp.get_attribute_settings(player_attrib_settings_path)
    # build player
    print("\nBuilding player")
    bcp.build_player(
        ndf,
        playerpath=str(player_dir),
        player_bucket=bucket,
        launch_local=True,
        upload_s3=True,
        proj_descr="VDL Project Template",
        proj_title="VDL Project Template",
        node_size="funding_column",
        sponsors=[],
        attrib_settings=attrib_settings,  # dictionary of attribute settings
        attrib_tooltips=attrib_tooltips,  # dictionary of attribute tooltips
    )
