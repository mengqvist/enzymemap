"""Provide the primary functions."""

from enzymemap import helpers_brenda, helpers_resolve_smiles, helpers_rdkit, helpers_map
import itertools
import pandas as pd
from typing import Dict, Tuple

def make_initial(file_loc: str, file_loc_inchi: str, file_loc_chebi: str, manual_corrections: bool = False) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """make_initial.

    Function to read BRENDA database and resolve names to SMILES.

    Args:
        file_loc (str): Location of BRENDA download text file
        file_loc_inchi (str): Location of BRENDA download of inchi keys
        file_loc_chebi (str): Location of BRENDA download of chebi keys
        manual_corrections (bool): Whether to add a set of manual corrections for SMILES entries. Do not use for custom datasets.

    Returns:
        Tuple containing a Pandas Dataframe of uncorrected, raw reactions and a dictionary mapping compounds to smiles
    """
    # parse the BRENDA flatfile
    df, compounds_df = helpers_brenda.parse_brenda(file_loc)

    # obtain SMILES from compound names ond use to update the dataframe
    compound_df = helpers_resolve_smiles.resolve_all(compounds_df, file_loc_inchi, file_loc_chebi)

    # stadardize the smiles and try to get the best representation for each compound
    compound_df = helpers_resolve_smiles.standardize_compound_df(compound_df)

    # apply manual corrections
    if manual_corrections:
        compound_df = helpers_resolve_smiles.manual_corrections_compounds(compound_df)

    # create a dictionary mapping compound names to smiles
    compound_to_smiles={}
    for i in compound_df.index:
        compound_to_smiles[compound_df['compound'][i]] = compound_df['smiles_neutral'][i]
    
    # Make all possible reactions from different compound_to_smiles results (for the different substrates and products)
    reactions = []
    for i in df.index:
        reacs = [".".join(x) for x in list(itertools.product(*[compound_to_smiles[x] for x in df['SUBSTRATES'][i]]))]
        prods = [".".join(x) for x in list(itertools.product(*[compound_to_smiles[x] for x in df['PRODUCTS'][i]]))]
        reaction = [">>".join(x) for x in list(itertools.product(*[reacs, prods]))]
        reaction = [helpers_rdkit.put_h_last(r) for r in reaction]
        reactions.append(reaction)
    df['POSSIBLE_RXNS'] = reactions

    # get dictonaries to convert between reduced and oxidized forms of compounds
    reduced_to_oxidized, oxidized_to_reduced = helpers_rdkit.get_strip_list(compound_to_smiles)
    
    # Correct and balance reactions
    cached = dict()
    print("Correcting and balancing")
    balanced_rxn_list = []
    for i in df.index:
        print(i, end='\r')
        if df['RXN_TEXT'][i] not in cached:
            rxns = helpers_rdkit.correct_reaction(df['POSSIBLE_RXNS'][i], df['RXN_TEXT'][i], reduced_to_oxidized, oxidized_to_reduced)
            rxns = [helpers_rdkit.put_h_last(r) for r in rxns]
            cached[df['RXN_TEXT'][i]] = rxns
        else:
            rxns = cached[df['RXN_TEXT'][i]]
        balanced_rxn_list.append(rxns)
    df['BALANCED_RXNS'] = balanced_rxn_list

    print("not resolvable:", sum([len(x) == 0 for x in df['POSSIBLE_RXNS']]),"out of",len(df),"entries")
    print("resolvable but unbalanced:", sum([len(x) == 0 for x in df['BALANCED_RXNS']])-sum([len(x) == 0 for x in df['POSSIBLE_RXNS']]),"out of",len(df),"entries")
    
    return df, compound_to_smiles

def map_group(df: pd.DataFrame, db_rules: pd.DataFrame) -> pd.DataFrame:
    """map_group.

    Processes a group of reactions, usually within an EC number, to obtain corrected reactions and atom-mappings via single step reactions, multi step reactions or suggested reactions.

    Args:
        df (pd.DataFrame): Pandas dataframe with current reactions within an EC number, as output by the make_initial function.
        db_rules (pd.DataFrame): Pandas dataframe of rules to use for mapping.

    Returns:
        pd.DataFrame: Pandas Dataframes of reactions
    """

    groups = helpers_map.get_groups(db_rules)
    groups.append({-1}) # For isomerase reactions

    df = df[['POSSIBLE_RXNS','ORIG_RXN_TEXT','REVERSIBLE','BALANCED_RXNS','NATURAL','ORGANISM','PROTEIN_REFS','PROTEIN_DB']].copy()
    df.columns = ['uncorrected_rxns', 'rxn_text', 'reversible','balanced_rxns','natural','organism','protein_refs','protein_db']
    df['source'] = None
    df['step'] = None
    df['mapped_rxns'] = [[] for _ in range(len(df))]
    df['rules'] = [[] for _ in range(len(df))]
    df['rule_ids'] = [[] for _ in range(len(df))]
    df['individuals'] = [[] for _ in range(len(df))]
    
        
    # Try single step map with corrected stereochem on balanced reactions
    print("Mapping single steps")
    for i in df.index:
        print(i,end='\r')
        if len(df['balanced_rxns'][i]) > 0:
            rxns, rules, rule_ids, indis = helpers_map.map(df['balanced_rxns'][i], db_rules, single=True)
            if len(rxns) > 0:
                df['source'][i] = 'direct'
                df['step'][i] = 'single'
                df['mapped_rxns'][i] = rxns
                df['rules'][i] = rules
                df['rule_ids'][i] = rule_ids
                df['individuals'][i] = indis

    # Try multi step map with corrected stereochem on balanced reactions
    print("Mapping multi steps")
    rule_ids_in_ec = list(set([item for sublist in df['rule_ids'].values for item in sublist if item != None]))
    for i in df.index:
        print(i,end='\r')
        if len(df['balanced_rxns'][i]) > 0 and len(df['mapped_rxns'][i]) == 0:
            if len(rule_ids_in_ec)==0:
                rxns, rules, rule_ids, indis = helpers_map.map(df['balanced_rxns'][i], db_rules, single=False)
            else:
                rxns, rules, rule_ids, indis = helpers_map.map(df['balanced_rxns'][i], db_rules.loc[rule_ids_in_ec], single=False)
            if len(rxns) > 0:
                df['source'][i] = 'direct'
                df['step'][i] = 'multi'
                df['mapped_rxns'][i] = rxns
                df['rules'][i] = rules
                df['rule_ids'][i] = rule_ids
                df['individuals'][i] = indis
            
    # Suggest for unbalanced or unmapped
    print("Suggesting reactions")
    reactions_in_ec = [item for sublist in df['mapped_rxns'].values for item in sublist if item != None]
    rule_ids_in_ec = list(set([item for sublist in df['rule_ids'].values for item in sublist if item != None]))
    if len(reactions_in_ec) > 0:
        templates, temp2h, temp2reac, template_s = helpers_map.make_templates_for_suggestions(reactions_in_ec)
        for i in df.index:
            print(i,end='\r')
            if len(df['mapped_rxns'][i]) == 0:
                try:
                    rxns = helpers_map.suggest_corrections(df['uncorrected_rxns'][i], templates, temp2h, temp2reac, template_s)
                except:
                    rxns = []
                if len(rxns) > 0:
                    rxns, rules, rule_ids, indis = helpers_map.map(rxns, db_rules.loc[rule_ids_in_ec], single=True)
                    if len(rxns) > 0:
                        df['source'][i] = 'suggested'
                        df['step'][i] = 'single'
                        df['mapped_rxns'][i] = rxns
                        df['rules'][i] = rules
                        df['rule_ids'][i] = rule_ids
                        df['individuals'][i] = indis

    # If multiple options: select best bond_edits
    print("Per entry, select best option")
    for i in df.index:
        print(i,end='\r')
        if len(df['mapped_rxns'][i]) > 0:
            df['mapped_rxns'][i], df['rules'][i], df['rule_ids'][i], df['individuals'][i] = helpers_rdkit.select_best(df['mapped_rxns'][i], df['rules'][i], df['rule_ids'][i], df['individuals'][i])
            
    # Judge quality based on rule frequency
    print("Judging quality of reaction")
    l = [item for sublist in df['rule_ids'].values for item in sublist if item != None]
    counts={}
    for group in groups:
        count=0
        for x in group:
            c = l.count(x)
            count += c
        if count != 0:
            for x in group:
                counts[x] = count/len(l)
    df['quality'] = [[counts[r] for r in df['rule_ids'][i]] for i in df.index]
    
    # Add reverse reactions for reversible cases and split into individual entries
    print("Adding reversible reactions")
    df['prob_rev'] =  helpers_map.probably_reversible(df, db_rules)
    df = helpers_map.make_final(df)
    print("Found", len(df), "reactions including possible duplicates")
    
    return df

def get_data() -> pd.DataFrame:
    """get_data.

    Downloads the processed EnzymeMap database dataframe.

    Returns:
        Pandas Dataframe of EnzymeMap
    """

    url = 'https://github.com/hesther/enzymemap/raw/master/data/processed_reactions.csv.gz'
    df = pd.read_csv(url)

    return df
