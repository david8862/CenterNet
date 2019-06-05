cd src
# train
python main.py ctdet --exp_id roborock_dla_384 --dataset roborock --num_epochs 70 --lr_step 45,60
# evaluate
python compute_mAP.py ctdet --exp_id roborock_dla_384 --dataset roborock --resume --classes_path roborock_classes.txt --annotation_file roborock_2007_test.txt --save_result
cd ..
