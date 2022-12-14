# imports
import dataframe_image as dfi
import os
import subprocess, os, warnings, utils
import pandas as pd
import numpy as np
warnings.filterwarnings("ignore")

def parse_vcf(args, tsv_file):

    # Read tsv
    df = pd.read_csv(tsv_file, sep="\t")

    # Discard SNPs in INDELs
    df = utils.discard_SNP_in_DEL(df)

    # Drop duplicates
    df.drop_duplicates(subset=["POS", "REF", "ALT"], keep="first", inplace=True)

    # Discard indel positions
    df = df[df.ALT.str.len() == 1]

    # Select SNPs with a minimum DP
    df = df[df.TOTAL_DP > args.min_DP]

    # Set gen of each SNP
    df["GEN"] = [utils.pos_to_gen(pos) for pos in df.POS]

    # Round to 3 ALT_FREQ
    df.ALT_FREQ = df.ALT_FREQ.round(3)

    # Set REF_FREQ
    df["REF_FREQ"] = df.parallel_apply(lambda row: 1 - row.ALT_FREQ, axis = 1)
    df.REF_FREQ = df.REF_FREQ.round(3)

    # Drop non relevant features
    df.drop(columns=["REGION", "REF_RV", "REF_QUAL", "ALT_RV",
                        "ALT_QUAL", "PVAL", "PASS", "GFF_FEATURE"], 
                        inplace=True)

    # Order columns
    df = df[["POS", "REF", "ALT", "TOTAL_DP", "REF_DP", "REF_FREQ",
            "ALT_DP", "ALT_FREQ", "REF_CODON", "REF_AA", "ALT_CODON",
            "ALT_AA", "GEN"]]
    
    return df

def get_alingment(args, script_dir, name_tsv, df, cov_file):

    # directories
    dir_name_tsv = os.path.join(script_dir, name_tsv)
    utils.check_create_dir(dir_name_tsv)

    # parse coverage file
    cov_d = utils.parse_covfile(cov_file)

    # Parse reference sequence
    ref_genome = args.reference
    l_ref_sequence, header, ref_sequence = utils.parse_fasta(ref_genome)

    # List with Sample1 (<ALT_FREQ), Sample1+2 and Sample2 (>ALT_FREQ)
    genomes = [l_ref_sequence.copy(), l_ref_sequence.copy(), l_ref_sequence.copy()]
    sequences = [l_ref_sequence.copy(), l_ref_sequence.copy(), l_ref_sequence.copy()]

    # ambiguous dictionary
    iupac =     {"AG": "R", "GA":"R",
                "CT":"Y", "TC":"Y",
                "GC":"S", "CG":"S",
                "AT":"W", "TA":"W",
                "GT":"K", "TG":"K",
                "AC":"M", "CA":"M"}
    
    # Dictionary to add coordinate: ALT; coordinate: ALT proportion
    base_dict = {}
    prop_dict = {}

    # List position where SNPs proportion is higher or lower 
    # mean_htz +- (std + extra_std)
    # Low certain segregation
    l_pos_low_certain = []

    # Compute mean htz proportion and std of majority allel
    HTZ_SNVs = df[(df.ALT_FREQ <= args.min_HOM) & (df.ALT_FREQ >= (1 - args.min_HOM))]
    # number htz SNPs
    n_HTZ_SNPs = HTZ_SNVs.shape[0]
    if n_HTZ_SNPs:
        # select upper proportion htz
        upper_HTZ_prop_l = HTZ_SNVs[["ALT_FREQ", "REF_FREQ"]].max(axis=1).to_list()
        # mean_htz and std
        mean_ALT_HTZ_prop = round(np.mean(upper_HTZ_prop_l), 2)
        std_ALT_HTZ_prop = round(np.std(upper_HTZ_prop_l), 2)
    else:
        mean_ALT_HTZ_prop = 0
        std_ALT_HTZ_prop = 0

    # If SNPs
    if df.shape[0]:
        for _, row in df.iterrows():

            # Conditional to check if more than one ALT in same coordinate
            # Compare current coordinate with previous one
            if row["POS"] not in base_dict:

                # Once we passed to next coordinate
                # We check ALT with higher proportion from previous coordinate
                # and write it into genome
                if len(base_dict) > 0:
                    segregate_SNP(base_dict, prop_dict, l_ref_sequence, args, mean_ALT_HTZ_prop, std_ALT_HTZ_prop, l_pos_low_certain, genomes, sequences, iupac)

                # New genome position
                base_dict = {}
                prop_dict = {}

                # Add ALT in coordinate
                base_dict[row["POS"]] = []
                base_dict[row["POS"]].append(row["ALT"])

                # Add ALT proportion
                prop_dict[row["POS"]] = []
                prop_dict[row["POS"]].append(row["ALT_FREQ"])

            elif row["POS"] in base_dict:
                base_dict[row["POS"]].append(row["ALT"])
                prop_dict[row["POS"]].append(row["ALT_FREQ"])
        
        # LAST SNP
        segregate_SNP(base_dict, prop_dict, l_ref_sequence, args, mean_ALT_HTZ_prop, std_ALT_HTZ_prop, l_pos_low_certain, genomes, sequences, iupac)
        
        # Check coverage
        for n in range(len(l_ref_sequence)):
            pos = n + 1
            # If min_DP == 0, we set cov_d[pos] == 0
            if cov_d[pos] < args.min_DP or cov_d[pos] == 0:
                for i in range(len(genomes)):
                    genome = genomes[i]
                    sequence = sequences[i]
                    genome[n] = "N"
                    sequence[n] = "N"

    # Store sequences to fasta files
    # out sequences dir
    out_seq_dir = os.path.join(dir_name_tsv, "Sequences")
    utils.check_create_dir(out_seq_dir)

    for n in ["1", "2"]:

        if n == "1":
            index_sequence = 0
        else:
            index_sequence = 2

        # Name sequence
        sample = open(out_seq_dir + "/" + name_tsv + "_%s.fasta" %(n), "w")
        to_write = ">" + name_tsv + "_%s\n" %(n) + "".join(sequences[index_sequence]) + "\n"
        sample.write(to_write)
        sample.close()

        # get tsv (VCF) and cov (COV)
        utils.fasta2compare(args, out_seq_dir, name_tsv + "_%s" %(n))

        if args.pangolin:
            subprocess.run(["pangolin", out_seq_dir + "/" + name_tsv + "_%s.fasta" %(n),
                                "--outdir", out_seq_dir,
                                "--outfile", name_tsv + "_%s_pangolin.csv" %(n),
                                "--max-ambig", "0.6"])
    
    # Specify low certain segregated SNPs
    out_file = open(out_seq_dir + "/" + "moderate_confidence_segregated_SNPs.csv", "w")
    to_write = "SNPs segregated with moderate confidence\n"
    for p in l_pos_low_certain:
        to_write += str(p) + "\n"
    out_file.write(to_write)
    out_file.close()
    
    # Align samples with mafft
    try:
        subprocess.call("cat %s > %s" %(ref_genome, os.path.join(out_seq_dir, "all.fasta")),
                        shell=True)
        subprocess.call("cat %s/*.fasta >> %s/all.fasta" %(out_seq_dir, out_seq_dir),
                        shell=True)
        subprocess.call("mafft --quiet --maxiterate 100 %s  > %s" %(os.path.join(out_seq_dir, "all.fasta"),
                        os.path.join(out_seq_dir, "all.aln")),
                        shell=True)
        subprocess.call("rm %s" %(os.path.join(out_seq_dir, "all.fasta")),
                        shell=True)

    except:
        print("MAFFT aligner is not installed")
        exit(1)

    # snipit
    if args.snipit:
        subprocess.run(["snipit", os.path.join(out_seq_dir, "all.aln"), "-f", "pdf",
                            "--flip-vertical", "-o",
                            os.path.join(out_seq_dir, name_tsv)])
        subprocess.call("rm %s" %(os.path.join(out_seq_dir, "all.aln")),
                        shell=True)

    # Get plot of each mixed sample
    aln2df(args, name_tsv, dir_name_tsv, genomes, l_ref_sequence, cov_d)

def segregate_SNP(base_dict, prop_dict, l_ref_sequence, args, mean_ALT_HTZ_prop, std_ALT_HTZ_prop, l_pos_low_certain, genomes, sequences, iupac):

    # current position (coordinate)
    position = list(base_dict.keys())[0]
    coordinate = position - 1

    bases = base_dict[position]
    proportion = prop_dict[position]

    # Add reference base and proportion
    bases.append(l_ref_sequence[coordinate])
    proportion.append(round(1 - sum(proportion), 3))

    # Order ascendent lists ALT SNPs
    zipped_lists = zip(proportion, bases)
    sorted_zipped_lists = sorted(zipped_lists)
    bases_sorted = [element for _, element in sorted_zipped_lists]
    proportion.sort()

    # ALT with higher proportion
    max_prop = proportion[-1]
    max_base = bases_sorted[-1]

    # ALT/REF with first lower proportion
    min_prop = proportion[-2]
    min_base = bases_sorted[-2]

    # Check if SNP is of low certain segregation
    if (max_base != l_ref_sequence[coordinate] or min_base != l_ref_sequence[coordinate]) and \
        max_prop < args.min_HOM:
        # If more than one ALT
        if max_prop < 0.5:
            max_prop_bis = 1 - max_prop
            if (max_prop_bis > mean_ALT_HTZ_prop + (std_ALT_HTZ_prop + args.max_extra_std_htz) or \
                max_prop_bis < mean_ALT_HTZ_prop - (std_ALT_HTZ_prop + args.max_extra_std_htz)):
                l_pos_low_certain.append(position)
        elif (max_prop > mean_ALT_HTZ_prop + (std_ALT_HTZ_prop + args.max_extra_std_htz) or \
            max_prop < mean_ALT_HTZ_prop - (std_ALT_HTZ_prop + args.max_extra_std_htz)):
            l_pos_low_certain.append(position)

    # If % high HTZ < 0.55 set ?
    if max_prop <= args.ambiguity:

        # min_seq1
        genomes[0][coordinate] = iupac[max_base + min_base] + " (" + str(min_prop) + ")"
        sequences[0][coordinate] = iupac[max_base + min_base]

        #max_seq2
        genomes[2][coordinate] = iupac[max_base + min_base] + " (" + str(max_prop) + ")"
        sequences[2][coordinate] = iupac[max_base + min_base]

        # seq1_seq2
        genomes[1][coordinate] = max_base + "/" + min_base
    
    # If Homo
    elif max_prop >= args.min_HOM:

        # min_seq1
        genomes[0][coordinate] = max_base 
        sequences[0][coordinate] = max_base

        # max_seq2
        genomes[2][coordinate] = max_base 
        sequences[2][coordinate] = max_base

        # seq1_seq2
        genomes[1][coordinate] = max_base
    
    # If HTZ
    else:
        # min_seq1
        genomes[0][coordinate] = min_base + " (" + str(min_prop) + ")"
        sequences[0][coordinate] = min_base

        #max_seq2
        genomes[2][coordinate] = max_base + " (" + str(max_prop) + ")"
        sequences[2][coordinate] = max_base

        # seq1_seq2
        genomes[1][coordinate] = max_base + "/" + min_base

def aln2df(args, name_tsv, dir_name_tsv, genomes, l_ref_sequence, cov_d):

    # out_aln_dir
    out_aln_dir = dir_name_tsv + "/ALN"
    utils.check_create_dir(out_aln_dir)

    # Convert alingment to dataframe
    coordinates = list(range(1, len(genomes[0]) + 1))
    df_aln = pd.DataFrame([l_ref_sequence] + genomes, columns = coordinates,
        index=["Reference", name_tsv + "_1", 
        name_tsv + "_1+2", name_tsv + "_2"])
    df_aln_t = df_aln.T
    df_aln_SNV = df_aln_t[(df_aln_t[name_tsv + "_1"] != "N") & 
                    ((df_aln_t["Reference"] != df_aln_t[name_tsv + "_1"]) | 
                    (df_aln_t["Reference"] != df_aln_t[name_tsv + "_2"]))].T
    
    # Set position depth
    position_dp = []
    position_l = []

    for column in df_aln_SNV.columns:
        position_l.append(column)
        position_dp.append(str(cov_d[column + 1]))
    
    # Concat to df_aln_SNV
    DP_df = pd.DataFrame()
    DP_df["Total_DP"] = position_dp
    DP_df.index = position_l
    df_aln_SNV = pd.concat([df_aln_SNV, DP_df.T], sort=False)
    df_concat = df_aln_SNV

    # Store df
    df_concat.to_csv("%s_aln.csv" %(out_aln_dir + "/" + name_tsv), sep=",")

    # Color Dataframe
    dfi.export(df_concat.style.apply(utils.color_df, axis = 0),
                    "%s_aln.png" %(out_aln_dir + "/" + name_tsv),
                    max_cols=-1)
    
    # Select only HTZ positions in Alingment
    df_concat_t = df_concat.T
    df_concat_HTZ = df_concat_t[(df_concat_t[name_tsv + "_1"] != df_concat_t[name_tsv + "_2"])]
    df_concat_HTZ = df_concat_HTZ.T

    # Store df
    df_concat_HTZ.to_csv("%s_HTZ_aln.csv" %(out_aln_dir + "/" + name_tsv), sep=",")

    # Color Dataframe
    dfi.export(df_concat_HTZ.style.apply(utils.color_df, axis = 0),
                    "%s_HTZ_aln.png" %(out_aln_dir + "/" + name_tsv),
                    max_cols=-1)
