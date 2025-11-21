import vdl_tools.network_tools.network_functions as net
import vdl_tools.shared_tools.embedding_network.embedding_network as en
from vdl_tools.tag2network.Network import LayoutNetwork as ln
from vdl_tools.tag2network.Network import ComputeClustering as cc
from vdl_tools.tag2network.Network import BuildNetwork as bn  # build network functions



def decorate_network(ndf, ldf, params):
    print("\nDecorating Network")

    if params.add_nodata:
        # add 'no data' to empty tags
        for col in params.tagcols_nodata:
            ndf[col] = ndf[col].fillna('No Data')
            ndf[col] = ndf[col].apply(lambda x: 'no data' if x == "" else x)


    # CLUSTER LEVEL SUMMARIES
    print("Adding cluster layout")
    # add clustered layout sized by degree
    layout_params = ln.ClusterLayoutParams(
        overlap_frac=0.2,
        max_expansion=2,
        scale_factor=1, 
        size_attr="Degree",
    )
    ln.add_layout(ndf, ldf, params=layout_params)

    return ndf, ldf



# #####################################
# Build network and add custom layouts and cluster summaries - then write file
add_nodata_cols = []
def build_decorate_network(
    df,
    linksPer,  # links per node
    nw_name,  # final filename for network
    labelcol='profile_name',  # column to use for node labels in mappr
    clusName="Theme",  # name of clusters
    textcol='Summary',
    cluster_naming_subject="investment portfolio",
    id_col='uuid',
    node_size_attr="funding_column",
):

    layout_params = ln.ClusterLayoutParams(
        overlap_frac=0.2,    # for cluster layout, fraction circle overlap
        max_expansion=1.5,  # for cluster layout, default = 1.5
        scale_factor=1,    # for cluster layout, default = 1
        size_attr=node_size_attr,  # for cluster layout, default = None
    )
    # clus_params = cc.ClusteringParams(method='louvain', merge_tiny=False)
    clus_params = cc.ClusteringParams(
        method='leiden',
        merge_tiny=True,
        reassign_top_n=10,
        reassign_size_ratio=15,
    )
    build_nw_params = (
        bn.BuildEmbeddingNWParams(
            linksPer=linksPer,
            n_tags=5,
            clusName=clusName,
            labelcol=labelcol,
            layout_params=layout_params,
            textcol=textcol,
            clus_params=clus_params,
            nw_name=nw_name,
            uid=id_col,
        )
    )

    ndf, ldf, sims = en.build_embedding_network(
        df,
        build_nw_params,
        subject=cluster_naming_subject,
        debug=False,
    )
    ndf = en.get_cluster_sentences_from_text(
        ndf,
        textcol,
        subject=cluster_naming_subject,
        model="gpt-4.1-mini",
    )
    # improve cluster names
    ndf = en.improve_one_sentences(ndf, subject=cluster_naming_subject, model="o3-mini")
    # custom project-specific decorate network, add cluster summaries
    ndf, ldf = decorate_network(ndf, ldf, build_nw_params)
    ndf['Cluster Theme'] = ndf['clus_sentence_reviewed']
    return ndf, ldf
