import re
from typing import Tuple, Dict, List, Set, Optional, Callable, Iterator
from collections import defaultdict
import pandas as pd

#Adappted from https://github.com/samgoldman97/brenda-parser

headers=['NATURAL_SUBSTRATE_PRODUCT','SUBSTRATE_PRODUCT']
tags={'NATURAL_SUBSTRATE_PRODUCT': 'NSP', 'SUBSTRATE_PRODUCT': 'SP'}

EC_RE = r"\d+.\d+.\d+.\d+"
UNIPROT_RE = r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}"
GENBANK_RE = r"[A-Z]{1,6}[0-9]{5,8}"
PROTEIN_DB_RE = r"(UniProt|GenBank|SwissProt)"
HEADER_RE = r"^\w+$"
TAG_RE = r"(^(?:[A-Z]|[0-9])+)(?:\t|$|_)"
ORG_RE = r"^(#(?:[0-9]+[, ]*?)+#)"  # some are misannotated w/ spaces
LIST_SPLIT_RE = r"[, ]"  # Split lists at commas or spaces
REF_ID_RE = r"^(<.*?>)"  # For reference id matches
REF_RE = r"^.*(<.*?>)[^<>]*$"  # Get LAST reference in the string
COMMENT_RE = r"(\({}.*\))".format(ORG_RE.replace(
    "^", ""))  # Enforce comment RE must have a ref at start
PRODUCT_COMMENT_RE = r"(\|{}.*\|)".format(ORG_RE.replace(
    "^", ""))  # Enforce comment RE must have a ref at start
COMMENT_SHELL_RE = r"^[\|\(](.+)[\|\)]$"  # Extract comment boundaries
COMMENT_SEP = r";"
SUBS_RE = r"([AC-IK-NP-TVWY])([0-9]+)([AC-HK-NP-TVWY]|I(?!ns))" # E.g. A212V/C220M substitutions; avoid C220InsA
INS_RE = "([AC-IK-NP-TVWY])([0-9]+)Ins([AC-IK-NP-TVWY])" # Capture insertions
INS_RE_2 = "ins([0-9]+)([AC-IK-NP-TVWY])" 
INS_RE_3 = "([AC-IK-NP-TVWY])([0-9]+) insertion"
DEL_RE= r"(?<!\w)del\s{0,1}([AC-IK-NP-TVWY])([0-9]+)"
NO_ACTIVITY_RE = r"no activity in"

def extract_orgs_desc(line: str) -> Tuple[list, str, dict, list]:
    """extract_orgs_desc.

    Extract organisms involved, description, comments, and references
    
    Example of a line being handled: 
        #3,7# 2-oxoglutarate + CoA + 2 oxidized ferredoxin = succinyl-CoA + CO2 + 2
        reduced ferredoxin + 2 H+ (#7# specific for 2-oxoglutarate <5>; #3# pure
        enzyme does not utilize other 2-keto acids, such as pyruvate,
        phenylglyoxylate, indolepyruvate, 2-oxobutanoate or 2-oxoisovalerate, as
        substrates <3>) <3,5>

        We want to pull out the organisms participating (#3,7#) and reduce the
        second part of the string to get rid of parantehtical and items

    Args:
        line (str): line

    Returns:
        Tuple[list, str, dict, list]: (orgs, desc, comments, refs)
    """

    # Format of line:
    # SY    #190# Pcal_1699 (#190# gene name <133>) <133>
    # First split to get number and parse that
    # Extract orgs
    org_match = re.match(ORG_RE, line)
    if org_match:
        orgs_str = org_match.group()
        orgs = [
            i.strip() for i in re.split(LIST_SPLIT_RE,
                                        org_match.group()[1:-1])
        ]
        # Replace rest of line
        line = re.sub(orgs_str, "", line, count=1)
    else:
        orgs = []

    # Extract references
    refs_match = re.search(REF_RE, line)
    if refs_match:
        refs_str = refs_match.groups()[0].strip()

        refs = [i.strip() for i in re.split(LIST_SPLIT_RE, refs_str[1:-1])]

        # Replace last occurence
        line = "".join(line.rsplit(refs_str, 1))
    else:
        refs = []

    # comments should be a dict with org num :
    # [("COMMENT" : desc, "REFS" : # [refs]), ("COMMENT" : desc, "REFS" : [refs])]
    com_dict = defaultdict(lambda: [])

    # Extract products comments
    prod_comments_match = re.search(PRODUCT_COMMENT_RE, line)
    if prod_comments_match:
        prod_comments = prod_comments_match.groups()[0]
        line = line.replace(prod_comments, "", 1)
        prod_comments = re.sub(COMMENT_SHELL_RE, r"\1", prod_comments)
        prod_comments = prod_comments.split(COMMENT_SEP)
        # Dangerous recursion
        prod_com_list = [extract_orgs_desc(j) for j in prod_comments]
        for com_orgs, com_desc, com_com, com_ref in prod_com_list:
            for com_org in com_orgs:
                com_dict[com_org].append({"COMMENT": com_desc, "REFS": com_ref})

    # else:
    #     prod_comments = ""

    # Now extract comments
    comments_match = re.search(COMMENT_RE, line)
    if comments_match:
        comments = comments_match.groups()[0]
        line = line.replace(comments, "", 1)
        comments = re.sub(COMMENT_SHELL_RE, r"\1", comments)
        comments = comments.split(COMMENT_SEP)
        # Dangerous recursion
        com_list = [extract_orgs_desc(j) for j in comments]
        for com_orgs, com_desc, com_com, com_ref in com_list:
            for com_org in com_orgs:
                com_dict[com_org].append({"COMMENT": com_desc, "REFS": com_ref})

    # else:
    #     comments = ""

    # Join comments and prod comments
    # comments = "".join([comments, prod_comments])
    desc = line.strip().replace("\n", " ")

    return (orgs, desc, com_dict, refs)


def extract_reaction(rxn: str) -> dict:
    """extract_reaction.

    Helper string to convert a reaction into its component substraters,
    products, and reversibility info 

    Args:
        rxn (str): rxn

    Returns:
        dict: Contains substrates, products, and reversibility
    """

    COMPOUNDS_RE = r" \+ "
    SUB_PROD_RE = r" = "
    REVERSIBLE_RE = r"\{\w*\} *$"

    reversibility = re.search(REVERSIBLE_RE, rxn)

    if reversibility:
        rxn = rxn.replace(reversibility.group(), "").strip()
        reversibility = reversibility.group()

    reversibility = (reversibility.strip()[1:-1]
                     if reversibility and len(reversibility) > 2 else "?")

    # Fix 'NAD' errors:
    if 'NADH+ H+' in rxn:
        rxn = rxn.replace('NADH+ H+', 'NADH + H+')
    if '(R)-3-hydroxybutanoyl-CoA +NADP+' in rxn:
        rxn = rxn.replace('(R)-3-hydroxybutanoyl-CoA +NADP+', '(R)-3-hydroxybutanoyl-CoA + NADP+')
    if ')poly(ethyleneglycol)-N6-(2-aminoethyl)-NADH' in rxn:
        rxn = rxn.replace(')poly(ethyleneglycol)-N6-(2-aminoethyl)-NADH', 'N6-poly(ethyleneglycol)-N6-(2-aminoethyl)-NADH')
    if '1,2-dehydro-N-methylcoclaurine NADPH' in rxn:
        rxn = rxn.replace('1,2-dehydro-N-methylcoclaurine NADPH', '1,2-dehydro-N-methylcoclaurine + NADPH')
    if '1,N6-etheno NAD+' in rxn:
        rxn = rxn.replace('1,N6-etheno NAD+', '1,N6-etheno-NAD+')
    if '17alpha,21-dihydroxy-5beta-pregnane-3,11,20-trione NADPH' in rxn:
        rxn = rxn.replace('17alpha,21-dihydroxy-5beta-pregnane-3,11,20-trione NADPH', '17alpha,21-dihydroxy-5beta-pregnane-3,11,20-trione + NADPH')
    if '3-hydroxy-3-methylglutaryl-CoA NADH' in rxn:
        rxn = rxn.replace('3-hydroxy-3-methylglutaryl-CoA NADH', '3-hydroxy-3-methylglutaryl-CoA + NADH')
    if '3alpha,12alpha-dihydroxy-7-oxo-5beta-cholanoyl taurine NADH' in rxn:
        rxn = rxn.replace('3alpha,12alpha-dihydroxy-7-oxo-5beta-cholanoyl taurine NADH', '3alpha,12alpha-dihydroxy-7-oxo-5beta-cholanoyl taurine + NADH')
    if '3aminopropanal+ NAD+' in rxn:
        rxn = rxn.replace('3aminopropanal+ NAD+', '3aminopropanal + NAD+')
    if '4-hydroxy-3-methylglutaryl-CoA NADH' in rxn:
        rxn = rxn.replace('4-hydroxy-3-methylglutaryl-CoA NADH', '4-hydroxy-3-methylglutaryl-CoA + NADH')
    if '5-hydroxy-3-methylglutaryl-CoA NADH' in rxn:
        rxn = rxn.replace('5-hydroxy-3-methylglutaryl-CoA NADH', '5-hydroxy-3-methylglutaryl-CoA + NADH')
    if 'H+ thio-NADPH' in rxn:
        rxn = rxn.replace('H+ thio-NADPH', 'H+ + thio-NADPH')
    if 'N6-CM-NAD+' in rxn:
        rxn = rxn.replace('N6-CM-NAD+', 'N6-carboxymethyl-NAD+')
    if 'NAD ' in rxn:
        rxn = rxn.replace('NAD ', 'NAD+ ')
    if 'NAD(+)' in rxn:
        rxn = rxn.replace('NAD(+)', 'NAD+')
    if 'NAD(H)' in rxn:
        rxn = rxn.replace('NAD(H)', 'NADH')
    if 'NAD+ 10' in rxn:
        rxn = rxn.replace('NAD+ 10', 'NAD+')
    if 'NAD+ formazan' in rxn:
        rxn = rxn.replace('NAD+ formazan', 'NAD+ + formazan')
    if 'NADP(+)' in rxn:
        rxn = rxn.replace('NADP(+)', 'NADP+')
    if 'NADP(H)' in rxn:
        rxn = rxn.replace('NADP(H)', 'NADPH')
    if 'NADP+ formazan' in rxn:
        rxn = rxn.replace('NADP+ formazan', 'NADP+ + formazan')
    if 'NADPH+ H+' in rxn:
        rxn = rxn.replace('NADPH+ H+', 'NADPH + H+')
    if 'NADPh' in rxn:
        rxn = rxn.replace('NADPh', 'NADPH')
    if 'NADp+' in rxn:
        rxn = rxn.replace('NADp+', 'NADP+')
    if 'propan-2-ol+ NAD+' in rxn:
        rxn = rxn.replace('propan-2-ol+ NAD+', 'propan-2-ol + NAD+')
    if 'thionicotinamide NAD+' in rxn:
        rxn = rxn.replace('thionicotinamide NAD+', 'thionicotinamide-NAD+')
    if 'thionicotinamideNADH' in rxn:
        rxn = rxn.replace('thionicotinamideNADH', 'thionicotinamide-NADH')
 
    split_prod = re.split(SUB_PROD_RE, rxn, 1)
    if len(split_prod) == 1:
        substrates = split_prod[0]
        products = ""
    elif len(split_prod) == 2:
        substrates, products = split_prod
    else:
        raise Exception(f"Unexpected number of reaction components in {rxn}")

    substrates = [
        re.sub(" \+$","",i.strip()) for i in re.split(COMPOUNDS_RE, substrates) if i.strip()
    ]
    products = [
        re.sub(" \+$","",i.strip()) for i in re.split(COMPOUNDS_RE, products) if i.strip()
    ]
    
    smi = []
    for x in substrates:
        if re.search("^\d+ ",x):
            num = re.search("^\d+ ",x).group().strip()
            s = re.sub("^\d+ ","",x)
            for i in range(int(num)):
                smi.append(s)
        else:
            smi.append(x)
    substrates = smi
        
    smi = []
    for x in products:
        if re.search("^\d+ ",x):
            num = re.search("^\d+ ",x).group().strip()
            s = re.sub("^\d+ ","",x)
            for i in range(int(num)):
                smi.append(s)
        else:
            smi.append(x)
    products = smi 


    ret_dict = {
        "SUBSTRATES": substrates,
        "PRODUCTS": products,
        "REVERSIBLE": reversibility,
        "RXN_TEXT": rxn
    }

    return ret_dict

def entry_lines(body: str, tag: str) -> Iterator[str]:
    """entry_lines.

    Helper iterator

    Args:
        body (str): Body of text 
        tag (str): Tag separating the body

    Return: 
        Iterator containing lines to be parsed 
    """
    # Helper function to replace line with spaces, then split at token
    split_at_token = lambda body, token: body.replace("\n\t", " ").split(token)

    lines = split_at_token(body, tag + "\t")

    for line in lines:
        line = line.strip()
        line = line.replace("\n", " ")

        if not line:
            continue

        yield line
        
def get_parser(header: str) -> Optional[Callable]:
    """get_parser.

    Return function that should parse this header
    
    Args:
        header (str): Header to parse
        
    Returns:
        Function that should parse this header
    """

    parser_list = {"PROTEIN": parse_protein}
    if header in parser_list:
        return parser_list[header]
    else:
        return None


def parse_protein(body: str, enzymes: dict, tag: str, ec_num: str,
                  **kwargs) -> None:
    """parse_protein.

    Args:
        body (str): body
        enzymes (dict): enzymes
        tag (str) : Tag associated with the entry header (without the tab)
        ec_num (str): ec number to use

    Returns:
        None:
    """
    # Split at thet tag
    for line in entry_lines(body, tag):
        org_num, pr_split, comments, refs = extract_orgs_desc(line)

        # Extract single org num
        if len(org_num) != 1:
            raise Exception(
                f"Found multiple organism numbers for protein {line}")
        else:
            org_num = org_num[0]

        # Search line for uniprot regex
        # Add support for multiple ref ids

        ref_ids = re.findall(UNIPROT_RE, pr_split)
        protein_db_default = "uniprot"

        # If empty set 
        if not ref_ids:
            ref_ids = re.findall(GENBANK_RE, pr_split)
            protein_db_default = "genbank"

        # extract which protein database
        protein_db = re.search(PROTEIN_DB_RE, pr_split, re.IGNORECASE)
        protein_db = protein_db.group().lower() if protein_db else protein_db

        # If protein db wasn't listed but we found an RE match for a ref id,
        # set it
        if not protein_db and ref_ids:   
            protein_db = protein_db_default

        # Handle no activity case
        no_activity = re.search(NO_ACTIVITY_RE, pr_split, re.IGNORECASE)
        no_activity = no_activity.group() if no_activity else no_activity
        is_negative = True if no_activity else False

        # Extract all extra categories
        if ref_ids:
            for ref_id in ref_ids: 
                pr_split = pr_split.replace(ref_id, "").strip()

            # Also replace the "and" token in case we have joiners
            pr_split = re.sub(" and ", "", pr_split, flags=re.IGNORECASE).strip()

        if protein_db:
            # Case insensitive!
            pr_split = re.sub(protein_db, "", pr_split, flags=re.IGNORECASE).strip()
        if no_activity:
            pr_split = pr_split.replace(no_activity, "").strip()

        organism = pr_split
        # Because we're defining organism numbers in this loop, comments must
        # be specific
        comments = comments.get(org_num, [])
        enzymes[org_num] = defaultdict(lambda: [])
        enzymes[org_num].update({
            "ec_num": ec_num,
            "organism": organism,
            "ref_ids": ref_ids,
            "protein_db": protein_db,
            "no_activity": is_negative,
            "refs": refs,
            "comments": comments,
        })


def process_entry(brenda_entry: str) -> List[dict]:
    """process_entry.

    Processes a single BRENDA entry

    Args:
        brenda_entry (str): BRENDA text 

    Return: 
        List of reactions
    """
    if "///" != brenda_entry[:3]:
        #Skip entry (0.0.0.0)
        return None
    brenda_entry = re.sub(r"\n\n+", "\n\n", brenda_entry).strip()
    cats=brenda_entry.split('\n\n')
    ec_num_text = cats[0].split('\n')[1].split('\t')[1]
    if "transferred" in ec_num_text or "eleted" in ec_num_text:
        print("skipped entry:", ec_num_text)
        return None
    ec_num = ec_num_text.split(" ")[0]
    reactions=[]
    enzymes = dict()
    general_stats = defaultdict(lambda: [])
    extra_args = {"ec_num": ec_num, "general_stats": general_stats}
    print('processing entry:',ec_num)
    for j in cats:
        j = j.strip()
        header, body = j.split("\n", 1)
        header, body = header.strip(), body.strip()
        tag = re.search(TAG_RE, body).groups(1)[0]
        
        parse_fn = get_parser(header)
        if parse_fn:
            parse_fn(body, enzymes, tag, header=header, **extra_args)

        if not header in headers:
            continue        
        
        for line in entry_lines(body, tags[header]):
            org_nums, desc, _, _ = extract_orgs_desc(line)

            orig_desc = desc
            #Split into two reactions for NAD(P)+/NAD(P)H:
            if desc.count('NAD(P)') == 2:
                desc = desc.replace('NAD(P)','NADP')
                desc2 = desc.replace('NADP','NAD')
                desc2 = extract_reaction(desc2)
                if '?' in desc2['SUBSTRATES'] or '?' in desc2['PRODUCTS'] or 'more' in desc2['SUBSTRATES']:
                    continue
                desc2['EC_NUM'] = ec_num
                desc2['ORIG_RXN_TEXT'] = orig_desc
                desc2['NATURAL'] = tags[header]=='NSP'
                for org_num in org_nums:
                    if org_num in enzymes.keys():
                        desc2['ORGANISM']=enzymes[org_num]['organism']
                        desc2['PROTEIN_REFS']=sorted(enzymes[org_num]['ref_ids'])
                        desc2['PROTEIN_DB']=enzymes[org_num]['protein_db']
                    else:
                        print("organism not found, setting information to None")
                        desc2['ORGANISM']=None
                        desc2['PROTEIN_REFS']=[]
                        desc2['PROTEIN_DB']=None
                    reactions.append(desc2.copy())
                
            #Correct if only one NAD(P):
            if desc.count('NAD(P)') == 1:
                if 'NADP' in desc:
                    desc = desc.replace('NAD(P)','NADP')
                else:
                    desc = desc.replace('NAD(P)','NAD')

            desc = extract_reaction(desc)
            if '?' in desc['SUBSTRATES'] or '?' in desc['PRODUCTS'] or 'more' in desc['SUBSTRATES']:
                continue
            desc['EC_NUM'] = ec_num
            desc['ORIG_RXN_TEXT'] = orig_desc
            desc['NATURAL'] = tags[header]=='NSP'
            for org_num in org_nums:
                if org_num in enzymes.keys():
                    desc['ORGANISM']=enzymes[org_num]['organism']
                    desc['PROTEIN_REFS']=sorted(enzymes[org_num]['ref_ids'])
                    desc['PROTEIN_DB']=enzymes[org_num]['protein_db'] 
                else:
                    print("organism not found, setting information to None")
                    desc['ORGANISM']=None
                    desc['PROTEIN_REFS']=[]
                    desc['PROTEIN_DB']=None
                reactions.append(desc.copy())
    return reactions


def parse_brenda(file_loc: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """parse_brenda.

    Parses a BRENDA text file

    Args:
        file_loc (str): Location of BRENDA download text file

    Return: 
        Tuple of two Pandas dataframes. The first dataframe contains EC numbers, lists of substrates and products, the reaction texts and the reversibility tag. The second dataframe contains compounds.
    """
    df = pd.DataFrame(columns=['EC_NUM','SUBSTRATES','PRODUCTS','RXN_TEXT','REVERSIBLE','ORIG_RXN_TEXT','NATURAL','ORGANISM','PROTEIN_REFS','PROTEIN_DB'])

    buff = ''
    with open(file_loc) as fp:
        new_line = fp.readline()
        while (new_line):
            # Get the next line if we have a blank
            if buff == "":
                buff += new_line
            else:
                # If we reach a new BRENDA header
                if r"///" in new_line:
                    reactions = process_entry(buff)
                    if reactions:
                        if len(df)>0:
                            df = pd.concat([df,pd.DataFrame(reactions)],ignore_index=True)
                        else:
                            df = pd.DataFrame(reactions)
                    buff = new_line
                elif len(buff) > 0:
                    buff += new_line
            new_line = fp.readline()

    df = df.loc[df.astype(str).drop_duplicates().index] # Drop duplicates (cast as str to avoid problem with list)
    df = df[~df['EC_NUM'].str.startswith('7')] # Drop transferases
    df = df.reset_index(drop=True) #Reset index

    print("Read in BRENDA")
    print(df)

    compounds=set()
    for x in df['SUBSTRATES']:
        for xx in x:
            compounds.add(xx)
    for x in df['PRODUCTS']:
        for xx in x:
            compounds.add(xx)

    compound_df = pd.DataFrame(compounds,columns=['compound'])

    return df, compound_df
