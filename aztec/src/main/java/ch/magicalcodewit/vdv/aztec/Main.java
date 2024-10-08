package ch.magicalcodewit.vdv.aztec;

import boofcv.abst.fiducial.AztecCodePreciseDetector;
import boofcv.alg.fiducial.aztec.AztecCode;
import boofcv.factory.fiducial.ConfigAztecCode;
import boofcv.factory.fiducial.FactoryFiducial;
import boofcv.io.image.ConvertBufferedImage;
import javax.imageio.ImageIO;
import boofcv.struct.image.GrayU8;

import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.List;

public class Main {
    public static void main(String[] args) {
        DataInputStream in = new DataInputStream(System.in);
        BufferedImage input;
        try {
           input = ImageIO.read(in);
        } catch (IOException e) {
            System.exit(-1);
            return;
        }
        GrayU8 gray = ConvertBufferedImage.convertFrom(input, (GrayU8) null);

        ConfigAztecCode config = new ConfigAztecCode();
        AztecCodePreciseDetector<GrayU8> detector = FactoryFiducial.aztec(config, GrayU8.class);

        detector.process(gray);

        List<AztecCode> detections = detector.getDetections();

        if (detections.size() == 0) {
            System.exit(-2);
            return;
        }

        DataOutputStream out = new DataOutputStream(System.out);
        try {
          out.write(detections.get(0).message.getBytes(StandardCharsets.ISO_8859_1));
        } catch (IOException e) {
            System.exit(-1);
            return;
        }
    }
}