(* https://dune.readthedocs.io/en/stable/howto/rule-generation.html#using-dynamic-include *)

let generate_rules base =
  Printf.printf
    {|
(rule
 (package hbt)
 (action
  (with-stdout-to
   %s_out.yaml
   (run %%{bin:hbt} --dump ../%s.html))))

(rule
 (package hbt)
 (alias runtest)
 (action
  (diff ../%s.yaml %s_out.yaml)))
|}
    base
    base
    base
    base

let () =
  Sys.readdir ".."
  |> Array.to_list
  |> List.filter_map (Filename.chop_suffix_opt ~suffix:".html")
  |> List.iter generate_rules
